import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from test_agents_repair import (  # noqa: F401
    ctx,
    folder,
    lab,
    model,
    revs,
    verifier_turns,
)

from brain import event_store, report
from brain.agents import bridge, router

pytestmark = pytest.mark.asyncio


@pytest.fixture
def api(lab, ctx, monkeypatch):  # noqa: F811
    monkeypatch.setattr(router, "get_lab", lambda repo, rev: lab)
    monkeypatch.setattr(router, "current_context", lambda: ctx)
    app = FastAPI()
    app.include_router(router.agents_router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://brain")


async def drain():
    while router._running:
        await asyncio.gather(*router._running)


def done():
    return [event["data"] for event in bridge.events() if event["type"] == "verification.done"]


async def test_bad_candidate_is_rejected_even_when_the_model_approves(api, model, lab, revs):  # noqa: F811
    model.turns["verifier"] = verifier_turns()
    response = await api.post("/chaos/bad-candidate")
    assert response.status_code == 202
    assert (await api.post("/chaos/bad-candidate")).status_code == 409
    await drain()
    result = done()[0]
    assert result["candidate_id"] == response.json()["candidate_id"]
    assert result["verdict"] == "REJECTED" and result["candidate"] == "fail"
    assert any("swallows" in issue for issue in result["issues"])
    proposed = next(e for e in bridge.events() if e["type"] == "candidate.proposed")
    assert proposed["data"]["by"] == "judge" and proposed["data"]["rev"] == response.json()["rev"]
    assert len(proposed["data"]["rev"]) == 40
    # The flawed fix really hides the failure, so this is not a straw man.
    assert "try { return" in proposed["data"]["diff"]
    assert await lab.resolve("main") == revs["broken"]


async def test_bad_candidate_is_rejected_when_the_model_is_down(api, model):  # noqa: F811
    model.turns["verifier"] = [[], [("submit", {})], [("submit", {})]]
    await api.post("/chaos/bad-candidate")
    await drain()
    assert done()[0]["verdict"] == "REJECTED"


async def test_unexpected_verifier_crash_is_visible(api, model):  # noqa: F811
    model.turns["verifier"] = [RuntimeError("boom")]
    await api.post("/chaos/bad-candidate")
    await drain()
    assert any("stays unverified" in (event["summary"] or "") for event in bridge.events())


async def test_bad_candidate_needs_an_incident(api, monkeypatch):
    monkeypatch.setattr(router, "current_context", lambda: None)
    assert (await api.post("/chaos/bad-candidate")).status_code == 404


async def test_spoil_falls_back_to_deleting_assertions(tmp_path):
    for name in ("src", "test"):
        (tmp_path / name).mkdir()
    (tmp_path / "src" / "a.js").write_text("export const x = 1;\n")
    test = tmp_path / "test" / "a.test.js"
    test.write_text("run();\nassert.equal(a, b);\n")
    assert "assertions" in router.spoil(tmp_path)
    assert test.read_text() == "run();\n"


@pytest.fixture
def incident(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_DB_PATH", str(tmp_path / "phoenix.sqlite3"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPO", "someone/phoenix-shop-demo")


async def emit(type_, data=None, actor="coordinator", incident_id="inc_9", summary="says | so"):
    await bridge.emit(type_, incident_id=incident_id, actor=actor, summary=summary, data=data)


def probe(ok, **extra):
    store = bridge.store()
    record = event_store.CheckoutProbe(
        ok=ok, status_code=200 if ok else 500, latency_ms=1, slot="a", revision="r"
    )
    store.record_probe(record)
    if extra:
        with sqlite3.connect(bridge.db_path()) as db:
            row = db.execute("SELECT id, payload FROM events ORDER BY id DESC LIMIT 1").fetchone()
            db.execute(
                "UPDATE events SET payload = ? WHERE id = ?",
                (json.dumps(json.loads(row[1]) | extra), row[0]),
            )
        db.close()


def at(type_, time):
    with sqlite3.connect(bridge.db_path()) as db:
        db.execute("UPDATE events SET occurred_at = ? WHERE type = ?", (time, type_))
    db.close()


async def story():
    await emit("chaos.break", incident_id="inc_old")
    probe(False)
    await emit("incident.opened", incident_id="inc_old")
    await emit("incident.restored", incident_id="inc_old")
    probe(False)
    await emit("chaos.break", incident_id="none")
    probe(False, cart_total_cents=1200)
    probe(True, cart_total_cents=9999)
    probe(False, cart_total_cents=2400)
    await emit("incident.opened")
    probe(False)
    await emit("incident.restored")
    probe(False)
    evidence = [{"kind": "diff", "ref": "src/shop.js:30", "url": None, "excerpt": "+ a\n```\nsneaky"}]
    finding = {"agent": "x", "claim": "bad | line", "confidence": 0.9, "suspect_type": "code",
               "suspect_ref": "abc", "evidence": evidence}
    await emit("finding.added", finding, actor="code_investigator")
    await emit("decision.made", {"remedy": "code_fix", "reasoning": "because"})
    await emit("agent.tool", {"tool": "noise"}, summary="NOISE")
    for name, verdict, mark in (("c1", "REJECTED", "fail"), ("c2", "VERIFIED", "pass")):
        data = {"candidate_id": name, "rev": "f" * 40, "diff": f"+ {name}", "by": "agent"}
        await emit("candidate.proposed", data, actor="repair")
        await emit("verification.done", {
            "candidate_id": name, "broken": "fail", "healthy": "pass", "candidate": mark,
            "unit_tests": "pass", "diff_review_ok": True, "issues": [], "verdict": verdict, "reason": "why",
        }, actor="verifier")
    at("incident.opened", "2026-11-23T10:00:00+00:00")
    at("incident.restored", "2026-11-23T10:00:07.500000+00:00")
    at("verification.done", "2026-11-23T10:01:30+00:00")


async def test_report_figures_come_from_events(incident):
    await story()
    text = report.render("inc_9")
    assert "| Time to restore | 7.5 s |" in text
    assert "| Time to verified fix | 90.0 s |" in text
    assert "| Failed checkouts | 3 |" in text
    assert "3600 cents across 2 of 3 carts" in text
    assert "| c1 | agent | ✗ | ✓ | ✗ | ✓ | ✓ | REJECTED | why |" in text
    assert "| c2 | agent | ✗ | ✓ | ✓ | ✓ | ✓ | VERIFIED | why |" in text
    assert "bad \\| line" in text and "````diff" in text and "NOISE" not in text
    assert "No release recorded." in text and "No finding." in text


async def test_report_never_invents_missing_figures(incident):
    await emit("agent.note")
    text = report.render("inc_9")
    assert text.count("not recorded") == 4
    with pytest.raises(LookupError):
        report.render("inc_unknown")


async def test_report_without_a_token_skips_the_pr(incident):
    await story()
    before = report.render("inc_9")
    assert await report.build_report("inc_9") == before
    ready = [event for event in bridge.events() if event["type"] == "report.ready"][0]
    assert ready["data"]["pr_url"] is None and "# Incident report" in ready["data"]["markdown"]
    assert any(event["summary"] == "PR skipped: no GitHub token" for event in bridge.events())


@pytest.fixture
def github(monkeypatch):
    state = SimpleNamespace(calls=[], fail=False)

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        state.calls.append((request.method, path, body))
        if state.fail:
            return httpx.Response(403)
        if request.method == "GET":
            if path.endswith("/phoenix-shop-demo") or path.endswith("/phoenix-shop-demo/"):
                return httpx.Response(200, json={"default_branch": "main"})
            if path.endswith("/heads/main"):
                return httpx.Response(200, json={"object": {"sha": "base"}})
            return httpx.Response(200, json=[]) if path.endswith("/pulls") else httpx.Response(404)
        return httpx.Response(201, json={"html_url": "https://github.com/someone/demo/pull/1"})

    original = httpx.AsyncClient
    monkeypatch.setenv("GITHUB_TOKEN", "t-secret")
    fake = SimpleNamespace(**{name: getattr(httpx, name) for name in ("HTTPStatusError", "RequestError", "Response")})
    fake.AsyncClient = lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(report, "httpx", fake)
    return state


async def test_report_opens_a_pull_request(incident, github):
    await story()
    await report.build_report("inc_9")
    ready = [event for event in bridge.events() if event["type"] == "report.ready"][0]
    assert ready["data"]["pr_url"].endswith("/pull/1")
    puts = {path.split("/contents/")[1]: body for method, path, body in github.calls if method == "PUT"}
    assert set(puts) == {"incidents/inc-9.md", "incidents/inc-9.patch"}
    assert base64.b64decode(puts["incidents/inc-9.patch"]["content"]) == b"+ c2"
    assert all(body["branch"] == "phoenix/inc-9" for body in puts.values())


async def test_report_refuses_the_submission_repository(incident, github, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "arshtomar583/ALTF4")
    await story()
    await report.build_report("inc_9")
    assert github.calls == []
    assert any("off limits" in (event["summary"] or "") for event in bridge.events())


async def test_github_failure_still_delivers_the_report(incident, github):
    github.fail = True
    await story()
    await report.build_report("inc_9")
    assert any(event["summary"] == "PR failed: GitHub answered 403" for event in bridge.events())
    assert any(event["type"] == "report.ready" for event in bridge.events())


async def test_report_route(api, incident):
    await story()
    response = await api.get("/incidents/inc_9/report")
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == report.render("inc_9")
    assert (await api.get("/incidents/nope/report")).status_code == 404
