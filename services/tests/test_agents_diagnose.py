import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from brain import llm
from brain.agents import code_investigator, pipeline, runtime_investigator
from brain.agents.base import CORRECTED, line_matches
from brain.agents.models import Evidence, FailingRequest, Finding, IncidentContext
from brain.lab import Lab
from brain.llm import AnswerRejected, EvidenceMismatch
from brain.tools import log_tools, search_tools
from brain.tools.git_tools import config_diff

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dev_incident import build_shop_repo, commit  # noqa: E402

pytestmark = pytest.mark.asyncio
REQUEST = FailingRequest(body={"sku": "demo-mug", "quantity": 1}, status=500)
ERROR = "TypeError: Cannot read properties of undefined (reading 'amount')"
CHANGED = (
    "-      totalCents: product.unitPriceCents * quantity - (discount?.amount ?? 0),\n"
    "+      totalCents: product.unitPriceCents * quantity - discount.amount,"
)
SHOP = "services/shoplite"
URL = "https://example.com/typeerror"


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    return tmp_path_factory.mktemp("diagnose")


@pytest.fixture(scope="module")
def revs(folder):
    revs = build_shop_repo(folder / "shop")
    repo = folder / "shop"
    shop = repo / "services" / "shoplite"
    (shop / ".env.shop").write_text("CURRENCY=INR\nPAYMENT_TOKEN=abc123\n")
    revs["env_before"] = commit(repo, "Add settings")
    (shop / ".env.shop").write_text("CURRENCY=INR\n")
    package = json.loads((shop / "package.json").read_text())
    package["dependencies"]["express"] = "^4.0.0"
    (shop / "package.json").write_text(json.dumps(package))
    revs["env_after"] = commit(repo, "Tidy settings")
    return revs


@pytest.fixture
def lab(folder, revs, monkeypatch):
    monkeypatch.setenv("PHOENIX_LOG_DIR", str(folder / "logs"))
    lab = Lab(folder / "shop", revs["healthy"], folder / "lab")
    yield lab
    lab.stop()


@pytest.fixture
def ctx(folder, revs, monkeypatch):
    monkeypatch.setenv("PHOENIX_DB_PATH", str(folder / "phoenix.sqlite3"))
    return IncidentContext(
        incident_id="inc-1", broken_rev=revs["broken"], healthy_rev=revs["healthy"],
        broken_slot="a", failing_request=REQUEST, shop_repo=folder / "shop",
    )


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for key in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def finding(**changes):
    fields = {
        "agent": "x", "claim": "c", "confidence": 0.9, "suspect_type": "code",
        "suspect_ref": None, "evidence": [],
    }
    return Finding(**fields | changes)


def code_finding(sha, excerpt=CHANGED, ref="src/shop.js:30"):
    return finding(suspect_ref=sha, evidence=[Evidence(kind="diff", ref=ref, excerpt=excerpt)])


def runtime_finding(url=URL, log=ERROR):
    evidence = [
        Evidence(kind="log", ref="lab", excerpt=log),
        Evidence(kind="config", ref=None, excerpt="No configuration changed"),
    ]
    if url:
        evidence.append(Evidence(kind="source", ref=None, excerpt="docs", url=url))
    return finding(evidence=evidence)


def seen(urls=(URL,)):
    seen = runtime_investigator.Seen()
    seen.log(f"{ERROR}\n    at file:///shop.js:30")
    seen.search([{"url": url} for url in urls] or {"error": "no key"})
    seen.config({})
    return seen


async def test_each_investigator_gets_only_its_own_tools(lab, ctx):
    code = {tool.name for tool in code_investigator.build_tools(lab, ctx)}
    runtime = {tool.name for tool in runtime_investigator.build_tools(lab, ctx)}
    assert code == {"git_log", "git_show", "git_diff", "git_bisect"}
    assert runtime == {"container_logs", "lab_logs", "config_diff", "web_search"}


async def test_config_diff_reports_a_removed_env_key_without_its_value(lab, revs):
    result = await config_diff(lab, revs["env_before"], revs["env_after"])
    assert {"file": f"{SHOP}/.env.shop", "key": "PAYMENT_TOKEN", "change": "removed"} in result["changes"]
    express = next(c for c in result["changes"] if c["key"] == "dependencies.express")
    assert express["after"] == '"^4.0.0"'
    assert "abc123" not in json.dumps(result)


async def test_config_diff_ignores_formatting(lab, folder, revs):
    repo = folder / "shop"
    package = repo / SHOP / "package.json"
    before = revs["env_after"]
    package.write_text(json.dumps(json.loads(package.read_text()), indent=4) + "\n\n")
    after = commit(repo, "Reformat")
    assert (await config_diff(lab, before, after))["config_changed"] is False


async def test_config_diff_is_quiet_when_only_code_changed(lab, revs):
    result = await config_diff(lab, revs["healthy"], revs["broken"])
    assert result["config_changed"] is False and f"{SHOP}/package.json" in result["files_compared"]


@pytest.fixture(autouse=True)
def web(monkeypatch):
    """Answer web requests locally; keyless search is rate-limited unless a test says otherwise."""
    state = SimpleNamespace(answer=lambda request: httpx.Response(429), requests=[])

    def handle(request):
        state.requests.append(request)
        return state.answer(request)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        search_tools, "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
            HTTPStatusError=httpx.HTTPStatusError,
            RequestError=httpx.RequestError,
        ),
    )
    return state


async def test_web_search_without_a_key_is_keyless(web):
    item = {"title": "T", "url": URL, "content": "C"}
    web.answer = lambda request: httpx.Response(200, json={"results": [item]})
    assert await search_tools.web_search("x") == [{"title": "T", "url": URL, "snippet": "C"}]
    headers = web.requests[0].headers
    assert headers["x-tavily-access-mode"] == "keyless" and "authorization" not in headers


async def test_rate_limited_keyless_search_returns_an_error():
    assert await search_tools.web_search("x") == {"error": "Keyless Tavily answered 429"}


async def test_web_search_tries_keys_before_keyless(monkeypatch, web):
    monkeypatch.setenv("TAVILY_API_KEY", "t-secret")
    monkeypatch.setenv("BRAVE_API_KEY", "b")

    def answer(request):
        if "tavily" in request.url.host:
            return httpx.Response(500)
        item = {"title": "T", "url": URL, "description": "D"}
        return httpx.Response(200, json={"web": {"results": [item] * 9}})

    web.answer = answer
    assert await search_tools.web_search("x") == [{"title": "T", "url": URL, "snippet": "D"}] * 5
    assert "x-tavily-access-mode" not in web.requests[0].headers

    monkeypatch.delenv("BRAVE_API_KEY")
    error = (await search_tools.web_search("x"))["error"]
    assert error == "Tavily answered 500; Keyless Tavily answered 500"


async def test_container_logs_fall_back_to_a_file_then_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOENIX_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "No logs for slot a" in (await log_tools.container_logs("a"))["error"]
    (tmp_path / "a.log").write_text(ERROR)
    assert await log_tools.container_logs("a") == ERROR
    assert "error" in await log_tools.container_logs("../b")


async def test_lab_logs_hold_the_replayed_error(lab, revs, folder):
    await lab.replay(revs["broken"], REQUEST)
    tool = next(tool for tool in log_tools.log_tools(lab, "a", revs["broken"]) if tool.name == "lab_logs")
    assert ERROR in await tool.fn(rev=revs["broken"])
    assert (folder / "logs" / f"lab-{revs['broken'][:12]}.log").exists()


async def test_code_guard(lab, revs):
    ran = [{}]
    await code_investigator.check_finding(code_finding(revs["culprit"]), lab, ran)
    rejected = [
        (finding(suspect_ref=revs["culprit"]), ran, "empty"),
        (code_finding(revs["culprit"]), [], "git_bisect"),
        (code_finding("deadbeef" * 5), ran, "not a commit"),
        (code_finding("main"), ran, "commit SHA"),
    ]
    for answer, bisected, message in rejected:
        with pytest.raises(AnswerRejected, match=message):
            await code_investigator.check_finding(answer, lab, bisected)


async def test_runtime_guard():
    check = runtime_investigator.check_finding
    check(runtime_finding(), seen())
    check(runtime_finding(url=None), seen(urls=()))
    rejected = [
        (finding(), seen(), "empty"),
        (runtime_finding(url=None), seen(), "source"),
        (runtime_finding(url="https://made.up"), seen(), "only URLs"),
        (runtime_finding(), runtime_investigator.Seen(), "web_search"),
    ]
    for answer, record, message in rejected:
        with pytest.raises(AnswerRejected, match=message):
            check(answer, record)


REAL = "+      totalCents: product.unitPriceCents * quantity - discount.amount,"


@pytest.mark.parametrize(
    ("quoted", "real", "expected"),
    [
        ("+ totalCents:   product.unitPriceCents * quantity - discount.amount,", REAL, True),
        ("product.unitPriceCents * quantity", REAL, True),
        (f"{REAL} // reads a missing discount", REAL, True),
        ("sku: “demo-mug” is “required”", "sku: 'demo-mug' is \"required\"", True),
        ("discount", REAL, False),
        ("- totalCents: product.unitPriceCents * quantity - discount.amount,", REAL, False),
        ("+ totalCents: product.price * count - coupon.value,", REAL, False),
    ],
)
async def test_line_matching(quoted, real, expected):
    assert line_matches(quoted, real) is expected


async def test_paraphrased_diff_is_reasked_then_corrected(lab, revs):
    check = code_investigator.check_evidence
    paraphrase = "+ totalCents: product.unitPrice * quantity - discount.amount"
    answer = code_finding(revs["culprit"], excerpt=paraphrase, ref="somewhere")
    with pytest.raises(EvidenceMismatch, match="discount.amount,"):
        await check(answer, lab, final=False)
    fixed, corrected = await check(answer, lab, final=True)
    assert corrected and fixed.evidence[0].excerpt == REAL
    shop = (await lab.git("show", f"{revs['culprit']}:{SHOP}/src/shop.js")).splitlines()
    line = next(n for n, text in enumerate(shop, 1) if "discount.amount" in text)
    assert fixed.evidence[0].ref == f"{SHOP}/src/shop.js:{line}"


async def test_matching_diff_needs_no_correction(lab, revs):
    spaced = "\n".join(" ".join(line.split()) for line in CHANGED.splitlines())
    answer = code_finding(revs["culprit"], excerpt=spaced, ref="wrong:1")
    fixed, corrected = await code_investigator.check_evidence(answer, lab, final=False)
    assert not corrected and fixed.evidence[0].ref.startswith(f"{SHOP}/src/shop.js:")


async def test_invented_evidence_is_never_dressed_up_as_real(lab, revs):
    answer = code_finding(revs["culprit"], excerpt="+ eval(zzz)")
    with pytest.raises(EvidenceMismatch):
        await code_investigator.check_evidence(answer, lab, final=True)
    with pytest.raises(EvidenceMismatch):
        runtime_investigator.check_evidence(runtime_finding(log="ENOMEM zzz"), seen(), final=True)


async def test_paraphrased_log_is_reasked_then_corrected():
    check = runtime_investigator.check_evidence
    exact, corrected = check(runtime_finding(log=ERROR.replace("'", '"')), seen(), final=False)
    assert not corrected and exact.evidence[0].excerpt == ERROR.replace("'", '"')
    answer = runtime_finding(log="TypeError: Cannot read property amount of undefined")
    with pytest.raises(EvidenceMismatch, match="reading 'amount'"):
        check(answer, seen(), final=False)
    fixed, corrected = check(answer, seen(), final=True)
    assert corrected and fixed.evidence[0].excerpt == ERROR


def script(monkeypatch, turns):
    """Give each agent its own fixed turns, chosen by its system prompt."""
    prompts = {}

    async def create(**kwargs):
        name = "code" if "Code Investigator" in kwargs["system"] else "runtime"
        prompts.setdefault(name, []).append(json.dumps(kwargs["messages"], default=str))
        call_name, args = turns[name].pop(0)
        call = SimpleNamespace(type="tool_use", id="c", name=call_name, input=args)
        usage = SimpleNamespace(
            input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[call], stop_reason="tool_use", usage=usage)

    fake = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "client", lambda: fake)
    for variable in ("MODEL_FAST", "MODEL_STRONG"):
        monkeypatch.setenv(variable, "claude-scripted")
    return prompts


def events(incident_id):
    from brain.agents import bridge

    every = [(event["type"], event | event["data"]) for event in bridge.events()]
    return [(type_, data) for type_, data in every if data["incident_id"] == incident_id]


async def test_diagnose_isolates_agents_reasks_caps_and_resumes(monkeypatch, lab, ctx, revs):
    monkeypatch.setattr(pipeline, "get_lab", lambda repo, rev: lab)
    monkeypatch.setattr(log_tools.lab_module, "log_dir", lambda: lab.log_dir)
    good = code_finding(revs["culprit"][:10]).model_dump()
    prompts = script(monkeypatch, {
        "code": [
            ("git_bisect", {"good": revs["healthy"], "bad": revs["broken"]}),
            ("web_search", {"query": "x"}),
            ("submit", good | {"evidence": []}),
            ("submit", good),
        ],
        "runtime": [
            ("lab_logs", {"rev": revs["broken"]}),
            ("web_search", {"query": ERROR}),
            ("config_diff", {"a": revs["healthy"], "b": revs["broken"]}),
            ("submit", runtime_finding(url=None).model_dump()),
        ],
    })
    result = await pipeline.diagnose(ctx)

    assert result["code"].suspect_ref == revs["culprit"]
    assert result["runtime"].confidence == 0.6
    assert "not available to you" in prompts["code"][2]
    assert "evidence is empty" in prompts["code"][3]
    assert "config_diff" not in prompts["code"][-1] and revs["culprit"] not in prompts["runtime"][-1]
    log = events("inc-1")
    assert [t for t, _ in log].count("finding.added") == 2
    for actor in (code_investigator.NAME, runtime_investigator.NAME):
        notes = [d["summary"] for t, d in log if t == "agent.note" and d["actor"] == actor]
        assert 2 <= len(notes) <= 4 and all(len(text) <= 90 for text in notes), notes

    # No scripted turns are left, so a rerun must come from the saved steps.
    assert await pipeline.diagnose(ctx) == result
    assert [t for t, _ in events("inc-1")].count("finding.added") == 2


async def test_paraphrasing_agent_keeps_its_finding(monkeypatch, lab, ctx, revs):
    paraphrase = "+ totalCents: product.unitPrice * quantity - discount.amount"
    bad = code_finding(revs["culprit"], excerpt=paraphrase).model_dump()
    prompts = script(monkeypatch, {
        "code": [
            ("git_bisect", {"good": revs["healthy"], "bad": revs["broken"]}),
            *[("git_log", {})] * 5,
            # A missing SHA and a misquote each get their own re-ask, past the turn limit.
            ("submit", bad | {"suspect_ref": "main"}),
            ("submit", bad),
            ("submit", bad),
        ],
    })
    ctx = ctx.model_copy(update={"incident_id": "inc-3"})
    finding = await code_investigator.investigate(lab, ctx, "Did the config change?")
    assert finding.evidence[0].excerpt == REAL
    assert "Did the config change?" in prompts["code"][0]
    assert any(data["summary"] == CORRECTED for _, data in events("inc-3"))


async def test_one_failed_investigator_does_not_lose_the_other(monkeypatch, lab, ctx, revs):
    monkeypatch.setattr(pipeline, "get_lab", lambda repo, rev: lab)
    bad = code_finding("deadbeef").model_dump()
    script(monkeypatch, {
        "code": [
            ("git_bisect", {"good": revs["healthy"], "bad": revs["broken"]}),
            ("submit", bad),
            ("submit", bad),
        ],
        "runtime": [
            ("lab_logs", {"rev": revs["broken"]}),
            ("web_search", {"query": ERROR}),
            ("config_diff", {"a": revs["healthy"], "b": revs["broken"]}),
            ("submit", runtime_finding(url=None).model_dump()),
        ],
    })
    result = await pipeline.diagnose(ctx.model_copy(update={"incident_id": "inc-2"}))
    assert result["code"] is None and result["runtime"] is not None
    assert any("could not reach" in data["summary"] for _, data in events("inc-2"))


async def test_agents_see_only_the_shop_in_a_shared_repository(lab, ctx, folder, revs):
    repo = folder / "shop"
    (repo / "faults").mkdir(exist_ok=True)
    (repo / "faults" / "answer.md").write_text("The fault is discount.amount")
    (repo / SHOP / "README.md").write_text("Edited together\n")
    head = commit(repo, "Mixed change")
    ctx = ctx.model_copy(update={"broken_rev": head})
    tools = {tool.name: tool for tool in code_investigator.build_tools(lab, ctx)}
    shown = await tools["git_show"].fn(rev=head)
    diff = await tools["git_diff"].fn(a=revs["healthy"], b=head)
    assert "Edited together" in shown and "answer" not in shown + diff

    (repo / "faults" / "answer.md").write_text("Another hint")
    commit(repo, "Outside the shop")
    assert "Outside the shop" not in await tools["git_log"].fn()


async def test_a_merged_fault_still_shows_its_patch(lab, ctx, folder, revs):
    from dev_incident import git

    repo = folder / "shop"
    git(repo, "checkout", "-q", "-b", "feature", revs["healthy"])
    shop = repo / SHOP / "src" / "shop.js"
    shop.write_text(shop.read_text().replace("(discount?.amount ?? 0)", "discount.amount"))
    commit(repo, "Simplify")
    git(repo, "checkout", "-q", "-b", "trunk", revs["healthy"])
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge feature", "feature")
    merge = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    tools = {tool.name: tool for tool in code_investigator.build_tools(lab, ctx)}
    result = await tools["git_bisect"].fn(good=revs["healthy"], bad=merge)
    assert result["first_bad_rev"] == merge and "+      totalCents" in result["diff"]


async def test_unrecorded_failure_status_is_measured(monkeypatch, lab, ctx):
    monkeypatch.setattr(pipeline, "get_lab", lambda repo, rev: lab)
    blank = ctx.model_copy(update={"failing_request": FailingRequest(body=REQUEST.body)})
    assert (await pipeline.observe(blank)).failing_request.status == 500


async def test_slot_logs_inside_docker_come_from_the_replay(lab, revs, monkeypatch, tmp_path):
    await lab.replay(revs["broken"], REQUEST)
    monkeypatch.setenv("PATH", str(tmp_path))

    async def resolve(rev):
        return rev

    monkeypatch.setattr(lab, "resolve", resolve)
    tool = log_tools.log_tools(lab, "a", revs["broken"])[0]
    broken = await tool.fn(slot="a")
    assert ERROR in broken["logs"] and "lab replay" in broken["source"]
    assert "error" in await tool.fn(slot="b")
    seen = runtime_investigator.Seen()
    seen.log(broken)
    assert any(ERROR in line for line in seen.logs)


async def test_invented_url_costs_a_reask_but_not_the_finding(monkeypatch, lab, ctx, revs, web):
    item = {"title": "T", "url": URL, "content": "C"}
    web.answer = lambda request: httpx.Response(200, json={"results": [item]})
    invented = runtime_finding().model_dump()
    invented["evidence"].append(
        {"kind": "source", "ref": None, "excerpt": "x", "url": "https://made.up/page"}
    )
    prompts = script(monkeypatch, {"runtime": [
        ("lab_logs", {"rev": revs["broken"]}),
        ("web_search", {"query": ERROR}),
        ("config_diff", {"a": revs["healthy"], "b": revs["broken"]}),
        ("submit", invented),
        ("submit", invented),
    ]})
    await lab.replay(revs["broken"], REQUEST)
    ctx = ctx.model_copy(update={"incident_id": "inc-url"})
    finding = await runtime_investigator.investigate(lab, ctx)
    assert URL in prompts["runtime"][4]
    assert [item.url for item in finding.evidence if item.url] == [URL]


async def test_missing_finding_is_not_retried_after_the_decision(monkeypatch, lab, ctx):
    from brain.agents import bridge

    monkeypatch.setattr(pipeline, "get_lab", lambda repo, rev: lab)
    ctx = ctx.model_copy(update={"incident_id": "inc-late"})

    async def decided():
        return {"decision": {}}

    await bridge.step(ctx.incident_id, "decide", decided)
    assert await pipeline.diagnose(ctx) == {"code": None, "runtime": None}
