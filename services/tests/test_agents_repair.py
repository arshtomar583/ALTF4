import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from brain import llm
from brain.agents import bridge, pipeline, repair, verifier
from brain.agents.models import Decision, FailingRequest, Finding, IncidentContext, Verification
from brain.lab import Lab
from brain.tools.fs_tools import Workspace
from brain.tools.lab_tools import Bench, check_test

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dev_incident import build_shop_repo, commit  # noqa: E402

pytestmark = pytest.mark.asyncio
SHOP = "services/shoplite"
BAD = "request.body.discount.code.trim().toUpperCase()"
FIX = "request.body?.discount?.code"
TOTAL = "totalCents: subtotalCents - discountCents"
TEST = """
import assert from "node:assert/strict";
import { test } from "node:test";
import request from "supertest";
import { createShop } from "../src/shop.js";
test("plain cart", async () => {
  const response = await request(createShop("t")).post("/checkout").send({ sku: "demo-mug", quantity: 1 });
  assert.equal(response.status, 200);
  assert.equal(response.body.totalCents, 1200);
});
"""
DECISION = Decision(
    cause_type="code", suspect_ref=None, remedy="code_fix", reasoning="SECRET-REASONING", uses_findings=[]
)


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    return tmp_path_factory.mktemp("repair")


@pytest.fixture(scope="module")
def revs(folder):
    return build_shop_repo(folder / "shop")


@pytest.fixture
def lab(folder, revs, tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_LOG_DIR", str(folder / "logs"))
    monkeypatch.setenv("PHOENIX_DB_PATH", str(tmp_path / "phoenix.sqlite3"))
    lab = Lab(folder / "shop", revs["healthy"], folder / "lab")
    monkeypatch.setattr(pipeline, "get_lab", lambda repo, rev: lab)
    yield lab
    lab.stop()


@pytest.fixture
def ctx(folder, revs, request):
    return IncidentContext(
        incident_id=f"inc-{abs(hash(request.node.name)) % 10**8}", broken_rev=revs["broken"],
        healthy_rev=revs["healthy"], broken_slot="a", shop_repo=folder / "shop",
        failing_request=FailingRequest(body={"sku": "demo-mug", "quantity": 1}, status=500),
    )


@pytest.fixture
def model(monkeypatch):
    """Script each agent's tool calls by its role; records every prompt."""
    state = SimpleNamespace(turns={"repair": [], "verifier": []}, prompts={"repair": [], "verifier": []})

    async def create(**kwargs):
        role = "repair" if "Repair agent" in kwargs["system"] else "verifier"
        state.prompts[role].append(json.dumps(kwargs["messages"], default=str))
        turn = state.turns[role].pop(0)
        if isinstance(turn, BaseException):
            raise turn
        calls = [SimpleNamespace(type="tool_use", id=n, name=n, input=args) for n, args in turn]
        usage = SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=calls, stop_reason="tool_use", usage=usage)

    fake = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "client", lambda: fake)
    monkeypatch.setenv("MODEL_STRONG", "claude-scripted")
    return state


async def broken_shop(lab, revs):
    return await lab.git("show", f"{revs['broken']}:{SHOP}/src/shop.js")


def repair_turns(content):
    return [
        [("write_file", {"path": "src/shop.js", "content": content})],
        [("run_unit_tests", {})],
        [("commit_candidate", {"message": "Fix total"})],
        [("submit", {"rationale": "SECRET-RATIONALE"})],
    ]


def verifier_turns(test=TEST, ok=True):
    return [
        [("write_regression_test", {"code": test})],
        [("submit", {"diff_review_ok": ok, "issues": []})],
    ]


def events():
    return [(event["type"], event | event["data"]) for event in bridge.events()]


async def candidate(lab, revs, name, edit):
    path, _ = await lab.candidate_branch(revs["broken"], name, 1)
    shop = path / "src" / "shop.js"
    shop.write_text(edit(shop.read_text()))
    return await lab.commit(path, name)


async def test_tool_lists(lab, revs):
    workspace = Workspace(lab, lab.root, revs["broken"])
    assert {t.name for t in workspace.tools()} == {
        "read_file", "write_file", "run_unit_tests", "commit_candidate",
    }
    assert {t.name for t in Bench(lab, revs, "", FailingRequest()).tools()} == {
        "lab_replay", "write_regression_test", "run_regression", "run_unit_tests", "read_candidate_diff",
    }


@pytest.mark.parametrize(
    "path",
    ["../x.js", "/etc/passwd", "test/checkout.test.js", "src/../test/a.js", "src/a.test.js", ".git",
     "package.json", "node_modules/express/index.js", "src\\..\\..\\x.js", "src/tests/a.js", ".env"],
)
async def test_write_file_is_confined_to_src(lab, revs, path):
    root, _ = await lab.candidate_branch(revs["broken"], "confined", 1)
    with pytest.raises(PermissionError):
        await Workspace(lab, root, revs["broken"]).write_file(path, "x")


async def test_write_file_does_not_follow_links_out(lab, revs, tmp_path):
    root, _ = await lab.candidate_branch(revs["broken"], "links", 1)
    (root / "src" / "out").symlink_to(tmp_path)
    with pytest.raises(PermissionError):
        await Workspace(lab, root, revs["broken"]).write_file("src/out/x.js", "x")


async def test_commit_needs_tests_on_the_final_content(lab, revs):
    root, _ = await lab.candidate_branch(revs["broken"], "commit", 1)
    workspace = Workspace(lab, root, revs["broken"])
    with pytest.raises(PermissionError, match="Unit tests"):
        await workspace.commit_candidate("early")
    fixed = (await workspace.read_file("src/shop.js")).replace(BAD, FIX)
    await workspace.write_file("src/shop.js", fixed)
    assert (await workspace.run_unit_tests())["ok"]
    await workspace.write_file("src/shop.js", fixed + "\n")
    with pytest.raises(PermissionError, match="Unit tests"):
        await workspace.commit_candidate("stale")


@pytest.mark.parametrize(
    "bad",
    ['import fs from "node:fs";', 'await import("node:fs");', "process.exit(0);",
     'const fs = require("fs");', "test.skip('x', () => {});", 'import "../package.json";',
     'import { x } from "../src/../../secret.js" ; fetch("http://x")'],
)
async def test_regression_test_cannot_reach_beyond_the_shop(bad):
    check_test(TEST)
    with pytest.raises(ValueError):
        check_test(TEST + bad)
    with pytest.raises(ValueError):
        check_test("const nothing = 1;")


def patch(*lines, file="src/shop.js"):
    return "\n".join([f"diff --git a/{file} b/{file}", "@@ -1,9 +1,9 @@", *lines])


HEALTHY = {"src/shop.js": f"const code = {FIX};\n"}
CHEATS = {
    "deleted assertion": (patch("-  assert.equal(a, 2400);"), "assertion"),
    "skipped test": (patch("+  test.skip('x', f);"), "Skips"),
    "test file edit": (patch("+  // x", file="test/checkout.test.js"), "outside src"),
    "config edit": (patch("+  x", file="package.json"), "outside src"),
    "hard-coded total": (patch("+    totalCents: 1200,"), "Hard-codes"),
    "hard-coded price": (patch("+  const unitPrice = 5;"), "Hard-codes"),
    "or zero": (patch("+  x: (price * quantity - discount) || 0,"), "|| 0"),
    "environment branch": (patch("+  if (process.env.NODE_ENV) y();"), "environment"),
    "restored test": (patch("-  x", file="test/new.test.js"), "outside src"),
    "no change": ("", "nothing"),
}
CATCHES = [
    "try { a(); } catch {}", "try { a(); } catch (e) { /* throw */ }", "try { a(); } catch (e) { // next(\n }",
    "try { t = a(); } catch (e) { t = 0; }", "try { a(); } catch (e) { response.json({ totalCents: 0 }); }",
    "p.catch(() => {})",
]


@pytest.mark.parametrize("name", CHEATS)
async def test_code_gate_rejects_each_cheat(name):
    diff, message = CHEATS[name]
    issues = verifier.review_diff(diff, HEALTHY, {"src/shop.js": "x", "package.json": "x"})
    assert any(message in issue for issue in issues), issues
    assert verifier.enforce(passing(), issues).verdict == "REJECTED"


@pytest.mark.parametrize("code", CATCHES)
async def test_code_gate_rejects_swallowed_errors(code):
    issues = verifier.review_diff(patch(f"+{code}"), HEALTHY, {"src/shop.js": code})
    assert any("swallows" in issue for issue in issues)


async def test_code_gate_allows_honest_fixes():
    for line in (
        f"+const code = {FIX};",
        "+  const off = discount?.amount || 0;",
        "+  try { a(); } catch (error) { next(error); }",
    ):
        text = line[1:]
        assert verifier.review_diff(patch(line), HEALTHY, {"src/shop.js": text}) == []
    restored = {"package.json": "{}"}
    assert verifier.review_diff(patch("+{}", file="package.json"), restored, restored) == []


def passing(**changes):
    fields = {
        "candidate_id": "c", "broken": "fail", "healthy": "pass", "candidate": "pass", "unit_tests": "pass",
        "diff_review_ok": True, "issues": [], "verdict": "VERIFIED", "reason": "",
    }
    return Verification(**fields | changes)


@pytest.mark.parametrize(
    "change",
    [{"broken": "pass"}, {"healthy": "fail"}, {"candidate": "fail"}, {"unit_tests": "fail"},
     {"diff_review_ok": False}],
)
async def test_gate_ignores_the_claimed_verdict(change):
    assert verifier.enforce(passing(verdict="REJECTED"), []).verdict == "VERIFIED"
    assert verifier.enforce(passing(**change), []).verdict == "REJECTED"


async def test_honest_fix_is_verified_from_measured_results(lab, ctx, revs, model):
    rev = await candidate(lab, revs, "honest", lambda text: text.replace(BAD, FIX))
    model.turns["verifier"] = verifier_turns()
    result = await verifier.verify(lab, ctx, "c", rev)
    assert result == passing(reason="The check fails on broken and passes on healthy and the candidate")


async def test_total_zero_cheat_is_rejected_even_if_the_model_approves(lab, ctx, revs, model):
    def cheat(text):
        fixed = text.replace(BAD, "(() => { try { return " + BAD + "; } catch { return undefined; } })()")
        return fixed.replace(TOTAL, "totalCents: code === undefined ? 0 : subtotalCents - discountCents")

    rev = await candidate(lab, revs, "cheat", cheat)
    weak = TEST.replace("  assert.equal(response.body.totalCents, 1200);\n", "")
    model.turns["verifier"] = verifier_turns(weak)
    result = await verifier.verify(lab, ctx, "c", rev)
    assert result.verdict == "REJECTED" and result.candidate == "fail" and not result.diff_review_ok
    assert any("'totalCents': 0" in issue for issue in result.issues)


async def test_model_review_can_still_reject(lab, ctx, revs, model):
    rev = await candidate(lab, revs, "review", lambda text: text.replace(BAD, FIX))
    model.turns["verifier"] = verifier_turns(ok=False)
    assert (await verifier.verify(lab, ctx, "c", rev)).verdict == "REJECTED"


async def test_useless_test_is_reasked_then_rejected(lab, ctx, revs, model):
    rev = await candidate(lab, revs, "useless", lambda text: text.replace(BAD, FIX))
    always = TEST.replace('.send({ sku: "demo-mug", quantity: 1 })', ".send({})").replace(
        "response.status, 200", "response.status, 400"
    ).replace("  assert.equal(response.body.totalCents, 1200);\n", "")
    model.turns["verifier"] = [*verifier_turns(always), verifier_turns()[1]]
    result = await verifier.verify(lab, ctx, "c", rev)
    assert "must fail on broken" in model.prompts["verifier"][2]
    assert result.broken == "pass" and result.verdict == "REJECTED"


async def test_verifier_outage_never_verifies(lab, ctx, revs, model):
    rev = await candidate(lab, revs, "outage", lambda text: text.replace(BAD, FIX))
    model.turns["verifier"] = [[], [("submit", {})], [("submit", {})]] * 2
    result = await verifier.verify(lab, ctx, "c", rev)
    assert (result.broken, result.healthy, result.candidate) == ("fail", "pass", "pass")
    assert result.verdict == "REJECTED" and "could not finish" in result.reason


async def test_verifier_outage_is_retried_before_the_fix_is_blamed(lab, ctx, revs, model):
    rev = await candidate(lab, revs, "flaky", lambda text: text.replace(BAD, FIX))
    model.turns["verifier"] = [[("submit", {})], [("submit", {})], *verifier_turns()]
    assert (await verifier.verify(lab, ctx, "c", rev)).verdict == "VERIFIED"


async def test_replay_uses_the_failing_request_unless_told_otherwise(lab, ctx, revs):
    bench = Bench(lab, revs, "", ctx.failing_request)
    assert (await bench.lab_replay("healthy"))["status"] == 200
    assert (await bench.lab_replay("healthy", '{"sku": "nope"}'))["status"] == 400


async def test_repair_accepts_repository_paths(lab, revs):
    root, _ = await lab.candidate_branch(revs["broken"], "paths", 1)
    workspace = Workspace(lab, root, revs["broken"])
    assert BAD in await workspace.read_file(f"{SHOP}/src/shop.js")
    with pytest.raises(FileNotFoundError, match="src/shop.js"):
        await workspace.read_file("shop.js")
    with pytest.raises(PermissionError):
        await workspace.write_file(f"{SHOP}/test/checkout.test.js", "x")


@pytest.fixture
def decided(monkeypatch):
    async def diagnose(_):
        return {"code": None, "runtime": None}

    async def decide(*_):
        return DECISION

    monkeypatch.setattr(pipeline, "diagnose", diagnose)
    monkeypatch.setattr(pipeline, "decide", decide)


async def test_rejected_fix_is_retried_with_feedback_and_secrets_stay_apart(
    lab, ctx, revs, model, decided
):
    text = await broken_shop(lab, revs)
    cheat = text.replace(BAD, FIX).replace(TOTAL, "totalCents: code === undefined ? 2400 : 2160")
    # These totals satisfy the shop's own tests, so only independent checks can catch it.
    model.turns["repair"] = [*repair_turns(cheat), *repair_turns(text.replace(BAD, FIX))]
    model.turns["verifier"] = [*verifier_turns(), *verifier_turns()]
    outcome = await pipeline.run_after_restore(ctx)

    assert outcome.status == "verified" and outcome.candidate_id.endswith("-c2")
    assert "The candidate answers 200" in model.prompts["repair"][4]
    verdicts = [d["verdict"] for t, d in events() if t == "verification.done"]
    assert verdicts == ["REJECTED", "VERIFIED"]
    seen = " ".join(model.prompts["verifier"])
    assert "SECRET" not in seen and "rationale" not in seen
    assert "plain cart" not in model.prompts["repair"][0]
    assert await lab.resolve("main") == revs["broken"]
    proposed = next(d for t, d in events() if t == "candidate.proposed")
    assert "rationale" not in proposed and proposed["by"] == "agent"


async def test_three_rejections_escalate_with_the_attempts(lab, ctx, revs, model, decided):
    text = await broken_shop(lab, revs)
    cheat = text.replace(BAD, FIX).replace(TOTAL, "totalCents: code === undefined ? 2400 : 2160")
    model.turns["repair"] = repair_turns(cheat) * 3
    model.turns["verifier"] = verifier_turns() * 3
    outcome = await pipeline.run_after_restore(ctx)
    assert outcome.status == "escalated"
    pack = next(d["evidence_pack_md"] for t, d in events() if t == "incident.escalated")
    assert pack.count("## Rejected candidate") == 3


async def test_killed_mid_verify_resumes_without_repairing_again(lab, ctx, revs, model, decided):
    text = await broken_shop(lab, revs)
    model.turns["repair"] = repair_turns(text.replace(BAD, FIX))
    model.turns["verifier"] = [verifier_turns()[0], KeyboardInterrupt()]
    with pytest.raises(BaseException):
        await pipeline.run_after_restore(ctx)
    # No repair turns are left, so the second run must reuse the saved candidate.
    model.turns["verifier"] = verifier_turns()
    assert (await pipeline.run_after_restore(ctx)).status == "verified"
    kinds = [t for t, _ in events()]
    assert kinds.count("candidate.proposed") == 1 and kinds.count("verification.done") == 1


async def test_lab_failure_escalates(ctx, lab, model, decided, monkeypatch):
    async def fail(*_):
        raise pipeline.LabError("no disk")

    monkeypatch.setattr(repair, "repair", fail)
    assert (await pipeline.run_after_restore(ctx)).status == "escalated"


async def test_config_restore_puts_healthy_config_back(lab, ctx, folder, revs):
    repo = folder / "config-shop"
    build_shop_repo(repo)
    (repo / SHOP / ".env.shop").write_text("A=1\n")
    healthy = commit(repo, "Config")
    (repo / SHOP / ".env.shop").write_text("")
    (repo / SHOP / ".env.extra").write_text("B=2\n")
    broken = commit(repo, "Break config")
    lab = Lab(repo, healthy, folder / "config-lab")
    ctx = ctx.model_copy(update={"healthy_rev": healthy, "broken_rev": broken})
    candidate = await repair.restore_config(lab, ctx, 1)
    assert await lab.git("diff", healthy, candidate.rev) == ""
    files = [f"{SHOP}/.env.shop", f"{SHOP}/.env.extra"]
    issues = verifier.review_diff(
        candidate.diff,
        await verifier._contents(lab, healthy, files),
        await verifier._contents(lab, candidate.rev, files),
    )
    assert issues == []
