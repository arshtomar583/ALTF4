"""The AI layer against the real event store, engine hooks and version-switch repository."""

import asyncio

import pytest
from test_agents_repair import (  # noqa: F401
    BAD,
    FIX,
    broken_shop,
    ctx,
    decided,
    folder,
    lab,
    model,
    repair_turns,
    revs,
    verifier_turns,
)

from brain import report
from brain.agents import bridge, pipeline, router
from brain.agents.models import PipelineOutcome
from brain.event_store import ApprovalRejected
from brain.incident import IncidentEngine
from brain.lab import Lab
from dev_incident import build_shop_repo, git
from version_switch.revisions import HEALTHY_REF, SlotRevisions

pytestmark = pytest.mark.asyncio
CART = {"sku": "demo-mug", "quantity": 1}


@pytest.fixture
def store(lab, folder, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PHOENIX_SHOP_REPO", str(folder / "shop"))
    monkeypatch.setattr(router, "get_lab", lambda repo, rev: lab)
    monkeypatch.setattr(pipeline, "_active", set())
    return bridge.store()


@pytest.fixture
def incident(store, revs):  # noqa: F811
    store.open_incident(
        broken_slot="a", broken_rev=revs["broken"], healthy_rev=revs["healthy"],
        failing_request={"method": "POST", "path": "/checkout", "body": CART},
    )
    store.mark_restored(store.current_incident(), "b")
    return store.current_incident()


def types():
    return [event["type"] for event in bridge.events()]


async def test_verified_candidate_is_approved_and_a_judges_is_refused(
    store, incident, lab, revs, model, decided  # noqa: F811
):
    text = await broken_shop(lab, revs)
    model.turns["repair"] = repair_turns(text.replace(BAD, FIX))
    model.turns["verifier"] = verifier_turns() * 2
    await pipeline.start_for_incident(incident)
    outcome = PipelineOutcome.model_validate(bridge.saved_step(incident.id, "outcome"))

    bad, result = await router.challenge(pipeline.current_context())
    assert result.verdict == "REJECTED"
    with pytest.raises(ApprovalRejected):
        store.approve_candidate(incident.id, bad.candidate_id)

    release = store.approve_candidate(incident.id, outcome.candidate_id)
    assert release.revision == outcome.candidate_rev and len(release.revision) == 40
    verified = [e for e in bridge.events() if e["type"] == "verification.done"][0]
    assert verified["actor"] == "verifier" and verified["data"]["rev"] == release.revision


async def test_candidate_commits_deploy_from_the_switch_repository(folder, monkeypatch):  # noqa: F811
    root = folder / "shared"
    revs = build_shop_repo(root / "source")  # noqa: F811
    git(root / "source", "checkout", "-q", "-B", "main", revs["healthy"])
    git(root / "source", "branch", "broken", revs["broken"])
    slots = SlotRevisions(root / "revisions", root / "source")
    slots.initialize()
    lab = Lab(slots.repo, revs["healthy"], root / "lab")  # noqa: F811

    path, branch = await lab.candidate_branch(revs["broken"], "inc_abc", 1)
    assert path.is_relative_to(root / "lab")
    shop = path / "src" / "shop.js"
    shop.write_text(shop.read_text().replace(BAD, FIX))
    sha = await lab.commit(path, "Fix")
    await lab.worktree(sha)

    assert slots._git(slots.repo, "rev-parse", f"refs/heads/{branch}") == sha
    assert branch == "phoenix/inc-abc-c1"
    author = slots._git(slots.repo, "log", "-1", "--format=%an <%ae>", sha)
    assert author == "PHOENIX Repair <repair@phoenix.local>"
    slots.checkout_rev("b", sha)
    assert slots.revision("b") == sha
    # The lab leaves the live slot and the healthy pointer alone.
    assert slots._git(slots.repo, "rev-parse", HEALTHY_REF) == revs["healthy"]
    assert slots._git(slots._worktree("a"), "rev-parse", "HEAD") == revs["healthy"]


async def test_only_one_pipeline_runs_per_incident(store, incident, monkeypatch):
    runs = []

    async def slow(_):
        runs.append(1)
        await asyncio.sleep(0.05)
        return PipelineOutcome(status="escalated")

    monkeypatch.setattr(pipeline, "run_after_restore", slow)
    await asyncio.gather(*(pipeline.start_for_incident(incident) for _ in range(3)))
    await pipeline.start_for_incident(incident)
    assert runs == [1]


async def test_restart_announces_recovery_and_resumes(
    store, incident, lab, revs, model, decided  # noqa: F811
):
    text = await broken_shop(lab, revs)
    model.turns["repair"] = repair_turns(text.replace(BAD, FIX))
    model.turns["verifier"] = [asyncio.CancelledError()]
    with pytest.raises(asyncio.CancelledError):
        await pipeline.start_for_incident(incident)

    model.turns["verifier"] = verifier_turns()
    await pipeline.resume()
    recovered = next(e for e in bridge.events() if e["type"] == "system.recovered")
    assert recovered["data"]["last_step"] == "proposed.c1"
    assert types().count("candidate.proposed") == 1
    assert types().count("incident.restored") == 1
    assert bridge.saved_step(incident.id, "outcome")["status"] == "verified"
    await pipeline.resume()
    assert types().count("system.recovered") == 1


async def test_kill_between_commit_and_announcement_keeps_one_revision_per_candidate(
    store, incident, lab, revs, model, decided, monkeypatch  # noqa: F811
):
    text = await broken_shop(lab, revs)
    model.turns["repair"] = repair_turns(text.replace(BAD, FIX))
    model.turns["verifier"] = verifier_turns()
    real = bridge.emit

    async def die(type_, **fields):
        await real(type_, **fields)
        if type_ == "candidate.proposed":
            raise asyncio.CancelledError

    monkeypatch.setattr(bridge, "emit", die)
    with pytest.raises(asyncio.CancelledError):
        await pipeline.start_for_incident(incident)
    monkeypatch.setattr(bridge, "emit", real)
    await pipeline.resume()
    proposals = {e["data"]["rev"] for e in bridge.events() if e["type"] == "candidate.proposed"}
    assert len(proposals) == 1
    assert store.approve_candidate(incident.id, f"{incident.id}-c1").revision in proposals


async def test_pipeline_crash_is_visible(store, incident, monkeypatch):
    async def crash(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "run_after_restore", crash)
    await pipeline.start_for_incident(incident)
    note = bridge.events()[-1]
    assert "unexpected problem" in note["summary"] and "boom" in note["data"]["error"]


async def test_restart_before_any_restore_or_incident_does_nothing(store):
    await pipeline.resume()
    assert types() == []


async def test_report_is_published_once(store, incident, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    await pipeline.report_release(incident)
    await pipeline.report_release(incident)
    assert types().count("report.ready") == 1
    assert "# Incident report" in report.render(incident.id)


async def test_engine_hooks_run_in_the_background(store, incident):
    engine = IncidentEngine(store, None, None)
    gate, seen = asyncio.Event(), []

    async def hook(record):
        await gate.wait()
        seen.append(record.id)

    async with engine.operation:
        engine.notify([hook], incident)
    assert seen == []
    gate.set()
    await asyncio.gather(*engine.hook_tasks)
    assert seen == [incident.id]


def release(store, incident, revision="f" * 40):  # noqa: F811
    store.emit(
        "candidate.proposed", {"candidate_id": "x", "rev": revision},
        incident_id=incident.id, actor="repair", summary="p",
    )
    store.emit(
        "verification.done", {"candidate_id": "x", "rev": revision, "verdict": "VERIFIED"},
        incident_id=incident.id, actor="verifier", summary="v",
    )
    record = store.approve_candidate(incident.id, "x")
    store.plan_release(record.id, "b")
    return record


async def test_release_closes_the_incident_and_another_can_open(store, incident, revs):  # noqa: F811
    record = release(store, incident)
    store.finish_release(incident, record, active_slot="a", active_rev="f" * 40)
    assert store.current_incident() is None
    closed = bridge.events()[-1]
    assert closed["type"] == "incident.closed" and closed["data"]["status"] == "resolved"
    assert closed["summary"] == "Incident closed: checkout healthy on slot A @ fffffff"
    store.open_incident(
        broken_slot="a", broken_rev=revs["broken"], healthy_rev="f" * 40,
        failing_request={"method": "POST", "path": "/checkout", "body": CART},
    )
    assert store.current_incident().id != incident.id


async def test_failed_release_keeps_the_incident_open(store, incident):
    record = release(store, incident)
    store.finish_release(incident, record, active_slot="b", active_rev="e" * 40, failure="probe failed")
    assert store.current_incident().status == "restored"


async def test_escalation_closes_the_incident_once(store, incident, monkeypatch):
    async def escalate(_):
        return PipelineOutcome(status="escalated")

    monkeypatch.setattr(pipeline, "run_after_restore", escalate)
    # mark_restored recorded which healthy slot is live.
    await pipeline.start_for_incident(incident)
    assert store.current_incident() is None
    closed = [e for e in bridge.events() if e["type"] == "incident.closed"]
    assert [e["data"]["status"] for e in closed] == ["escalated"]
    assert "slot B" in closed[0]["summary"]
    pipeline.close_escalated(incident.id)
    assert types().count("incident.closed") == 1


async def test_restart_between_escalation_and_closing_still_closes(store, incident):
    async def outcome():
        return PipelineOutcome(status="escalated").model_dump()

    await bridge.step(incident.id, "outcome", outcome)
    await pipeline.resume()
    assert store.current_incident() is None


async def test_judge_waits_for_the_pipeline(store, incident, monkeypatch):
    monkeypatch.setattr(pipeline, "_active", {incident.id})
    assert pipeline.is_busy(incident.id)
    store.emit(
        "verification.done", {"candidate_id": "x", "verdict": "VERIFIED"},
        incident_id=incident.id, actor="verifier", summary="v",
    )
    assert not pipeline.is_busy(incident.id)
