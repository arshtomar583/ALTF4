from types import SimpleNamespace

import pytest

from brain import llm
from brain.agents import bridge, coordinator, pipeline
from brain.agents.models import Evidence, FailingRequest, Finding, IncidentContext

pytestmark = pytest.mark.asyncio
SHA = "a" * 40
GOOD = {
    "cause_type": "code", "suspect_ref": "x", "remedy": "code_fix", "uses_findings": [],
    "reasoning": "code_investigator found the commit and runtime_investigator saw its error.",
}


def code(confidence=0.9, **changes):
    fields = {
        "agent": "code_investigator", "claim": "A commit reads a missing discount",
        "confidence": confidence, "suspect_type": "code", "suspect_ref": SHA,
        "evidence": [Evidence(kind="diff", ref="src/shop.js:30", excerpt="+ discount.code")],
    }
    return Finding(**fields | changes)


def runtime(confidence=0.8, **changes):
    fields = {
        "agent": "runtime_investigator", "claim": "Checkout throws a TypeError",
        "confidence": confidence, "suspect_type": "code",
        "suspect_ref": "file:///lab/src/shop.js:30:60",
        "evidence": [Evidence(kind="log", ref="lab", excerpt="TypeError")],
    }
    return Finding(**fields | changes)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DB_PATH", str(tmp_path / "phoenix.sqlite3"))


@pytest.fixture
def model(monkeypatch):
    """Script the coordinator's submissions; records every request."""
    state = SimpleNamespace(turns=[], sent=[])

    async def create(**kwargs):
        state.sent.append(kwargs)
        call = SimpleNamespace(type="tool_use", id="c", name="submit", input=state.turns.pop(0))
        usage = SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[call], stop_reason="tool_use", usage=usage)

    fake = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "client", lambda: fake)
    for variable in ("MODEL_FAST", "MODEL_STRONG"):
        monkeypatch.setenv(variable, "claude-scripted")
    return state


class FollowUp:
    def __init__(self, result=None):
        self.result, self.calls = result, []

    async def __call__(self, key, question):
        self.calls.append((key, question))
        if self.result is None:
            raise llm.AgentFailed("no")
        return self.result


def events():
    return [(event["type"], event | event["data"]) for event in bridge.events()]


async def run(code_finding, runtime_finding, follow_up=None):
    follow_up = follow_up or FollowUp()
    findings = {"code": code_finding, "runtime": runtime_finding}
    decision, pack = await coordinator.decide("inc-1", findings, follow_up)
    kinds = [type_ for type_, _ in events() if type_ in ("decision.made", "incident.escalated")]
    assert kinds == (["incident.escalated"] if pack else ["decision.made"])
    assert (decision.remedy == "escalate") == bool(pack)
    return decision, pack, follow_up


async def test_agreeing_findings_are_accepted(model):
    model.turns = [GOOD]
    decision, _, follow_up = await run(code(0.6), runtime(0.6))
    assert decision.remedy == "code_fix" and decision.suspect_ref == SHA
    assert decision.uses_findings == ["code_investigator", "runtime_investigator"]
    assert not follow_up.calls
    assert [tool["name"] for tool in model.sent[0]["tools"]] == ["submit"]


async def test_conflict_asks_the_more_confident_investigator_then_accepts(model):
    model.turns = [GOOD]
    conflicting = runtime(0.7, suspect_type="config", suspect_ref="CURRENCY")
    decision, _, follow_up = await run(code(0.65), conflicting, FollowUp(runtime(0.8)))
    assert follow_up.calls == [("runtime", code().claim)]
    assert decision.remedy == "code_fix"


async def test_conflict_that_survives_the_follow_up_escalates(model):
    conflicting = runtime(0.7, suspect_type="config", suspect_ref="CURRENCY")
    decision, pack, follow_up = await run(code(0.9), conflicting, FollowUp(code(0.95)))
    assert follow_up.calls == [("code", conflicting.claim)]
    assert not model.sent
    assert "different causes" in decision.reasoning
    assert "Code Investigator" in pack and "Runtime Investigator" in pack and "TypeError" in pack


async def test_both_weak_gets_one_follow_up_then_accepts(model):
    model.turns = [GOOD]
    decision, _, follow_up = await run(code(0.4), runtime(0.3), FollowUp(code(0.9)))
    assert follow_up.calls == [("code", runtime().claim)]
    assert decision.remedy == "code_fix"


async def test_both_weak_escalates_when_the_follow_up_is_weak_too(model):
    _, _, follow_up = await run(code(0.4), runtime(0.3), FollowUp(code(0.45)))
    assert len(follow_up.calls) == 1 and not model.sent


async def test_strong_finding_with_an_agreeing_weak_one_is_accepted(model):
    model.turns = [GOOD]
    decision, _, follow_up = await run(code(0.9), runtime(0.4))
    assert decision.remedy == "code_fix" and not follow_up.calls


async def test_agreeing_but_lukewarm_findings_get_a_follow_up(model):
    decision, _, follow_up = await run(code(0.55), runtime(0.6), FollowUp(runtime(0.65)))
    assert follow_up.calls == [("runtime", code().claim)]
    assert decision.remedy == "escalate"


async def test_failed_follow_up_keeps_the_first_findings_and_escalates(model):
    decision, _, _ = await run(code(0.65), runtime(0.55), FollowUp())
    assert "even after a follow-up" in decision.reasoning


@pytest.mark.parametrize(("finding", "key"), [(code(0.7), "code"), (runtime(0.7), "runtime")])
async def test_single_strong_finding_decides(model, finding, key):
    model.turns = [GOOD | {"reasoning": f"Only {finding.agent} reported."}]
    pair = {"code": None, "runtime": None} | {key: finding}
    decision, _, _ = await run(pair["code"], pair["runtime"])
    assert decision.remedy == "code_fix" and decision.suspect_ref == finding.suspect_ref
    assert decision.uses_findings == [finding.agent]


async def test_single_weak_finding_escalates(model):
    _, pack, _ = await run(code(0.69), None)
    assert not model.sent and "No finding." in pack


async def test_no_findings_escalate(model):
    await run(None, None)
    assert not model.sent


async def test_reasoning_must_name_both_agents(model):
    vague = GOOD | {"reasoning": "The commit is bad."}
    model.turns = [vague, GOOD]
    decision, _, _ = await run(code(), runtime())
    assert decision.remedy == "code_fix" and len(model.sent) == 2


async def test_reasoning_that_never_names_them_escalates(model):
    vague = GOOD | {"reasoning": "The commit is bad."}
    model.turns = [vague, vague]
    decision, _, _ = await run(code(), runtime())
    assert "could not decide" in decision.reasoning


async def test_model_cannot_pick_a_remedy_the_findings_contradict(model):
    wrong = GOOD | {"cause_type": "config", "remedy": "config_restore"}
    model.turns = [wrong, GOOD]
    assert (await run(code(), runtime()))[0].remedy == "code_fix"


async def test_model_may_escalate(model):
    model.turns = [GOOD | {"remedy": "escalate", "cause_type": "unknown"}]
    assert (await run(code(), runtime()))[1]


async def test_dependency_findings_restore_config(model):
    model.turns = [GOOD | {"cause_type": "config", "remedy": "config_restore"}]
    decision, _, _ = await run(
        code(suspect_type="dependency"), runtime(suspect_type="config", suspect_ref="express")
    )
    assert decision.remedy == "config_restore"


async def test_same_suspect_compares_files():
    assert coordinator.same_suspect(code(), runtime())
    assert coordinator.same_suspect(code(), runtime(suspect_ref=None))
    assert not coordinator.same_suspect(code(), runtime(suspect_ref="src/server.js:3"))
    assert not coordinator.same_suspect(code(), runtime(suspect_ref="src/myshop.js:3"))
    unknown = {"suspect_type": "unknown"}
    assert not coordinator.same_suspect(code(**unknown), runtime(**unknown))


async def test_pipeline_saves_the_decision(model, tmp_path):
    model.turns = [GOOD]
    ctx = IncidentContext(
        incident_id="inc-1", broken_rev="b", healthy_rev="a", broken_slot="a",
        failing_request=FailingRequest(status=500), shop_repo=tmp_path,
    )
    findings = {"code": code(), "runtime": runtime()}
    first = await pipeline.decide(ctx, findings)
    assert await pipeline.decide(ctx, findings) == first
    assert len(model.sent) == 1
