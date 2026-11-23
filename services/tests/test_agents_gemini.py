"""Drive the real Gemini SDK against a local server, so the wire format is checked."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from brain import llm
from brain.agents import bridge
from brain.agents.models import Decision, Finding

pytestmark = pytest.mark.asyncio
DECISION = {
    "cause_type": "code", "suspect_ref": None, "remedy": "code_fix", "reasoning": "r", "uses_findings": [],
}


def say(*calls, reason="STOP"):
    parts = [
        {"functionCall": {"id": f"id-{name}", "name": name, "args": args}, "thoughtSignature": "c2ln"}
        for name, args in calls
    ]
    return 200, {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": reason}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "thoughtsTokenCount": 2},
    }


def fail(code, *details):
    return code, {"error": {"code": code, "message": "no", "status": "X", "details": list(details)}}


@pytest.fixture
def server(tmp_path, monkeypatch):
    turns, sent = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["content-length"]))
            sent.append((self.path, json.loads(body)))
            code, reply = turns.pop(0)
            data = json.dumps(reply).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    http = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    monkeypatch.setenv("PHOENIX_DB_PATH", str(tmp_path / "phoenix.sqlite3"))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{http.server_port}")
    monkeypatch.setenv("MODEL_FALLBACKS", "")
    llm.gemini_client.cache_clear()
    monkeypatch.setattr(llm, "_busy_until", {})
    monkeypatch.setattr(llm, "limiter", llm.Limiter())
    monkeypatch.setenv("PHOENIX_LLM_RPM", "1000")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield turns, sent
    http.shutdown()
    http.server_close()
    llm.gemini_client.cache_clear()


async def run(tools=(), **extra):
    return await llm.run_agent(
        name="coordinator", system="s", user="u", tools=list(tools), model="gemini-test",
        output_model=Decision, incident_id="i", **extra,
    )


async def test_tool_round_trip_keeps_ids_and_thought_signatures(server):
    turns, sent = server

    async def echo(text):
        return text

    turns += [say(("echo", {"text": "hi"}), ("nope", {})), say(("submit", DECISION))]
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    assert (await run([llm.Tool("echo", "Echo", schema, echo)])).remedy == "code_fix"

    path, first = sent[0]
    assert "gemini-test:generateContent" in path
    assert first["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    user, model, results = sent[1][1]["contents"]
    assert [part["thoughtSignature"].rstrip("=") for part in model["parts"]] == ["c2ln", "c2ln"]
    answers = {p["functionResponse"]["id"]: p["functionResponse"]["response"] for p in results["parts"]}
    assert answers["id-echo"] == {"output": "hi"}
    assert "not available" in answers["id-nope"]["error"]


async def test_invalid_answer_is_reasked(server):
    turns, sent = server
    turns += [say(("submit", {})), say(("submit", DECISION))]
    await run()
    response = sent[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "Invalid answer" in response["error"]


async def test_malformed_call_is_nudged(server):
    turns, sent = server
    turns += [say(reason="MALFORMED_FUNCTION_CALL"), say(("submit", DECISION))]
    await run()
    assert "submit" in sent[1][1]["contents"][-1]["parts"][0]["text"]


async def test_rate_limit_waits_as_asked_and_retries_once(server, monkeypatch):
    turns, sent = server
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    turns += [fail(429, {"retryDelay": "7s"}), say(("submit", DECISION))]
    await run()
    assert [wait for wait in waits if wait != 0.05] == [7]
    assert any(event["summary"] == "Waiting for model quota (7s)…" for event in bridge.events())

    turns += [fail(503), fail(503)]
    with pytest.raises(llm.AgentFailed, match="503"):
        await run()


async def test_busy_model_hands_over_and_says_so(server, monkeypatch):
    turns, sent = server

    async def sleep(_):
        pass

    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    monkeypatch.setenv("MODEL_FALLBACKS", "gemini-test,gemini-second,gemini-third")
    quota = fail(429, {"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]})
    turns += [say(("nope", {})), fail(503), fail(503), quota, say(("submit", DECISION))]
    await run()
    models = [path.split("/models/")[1].split(":")[0] for path, _ in sent]
    assert models == ["gemini-test"] * 3 + ["gemini-second", "gemini-third"]
    assert sent[-1][1]["contents"] == sent[1][1]["contents"]
    last = bridge.events()[-1]
    assert last["data"]["usage"]["model"] == "gemini-third" and "gemini-third" in last["summary"]


async def test_busy_model_is_skipped_by_the_next_agent(server, monkeypatch):
    turns, sent = server
    monkeypatch.setenv("MODEL_FALLBACKS", "gemini-second")
    turns += [fail(429, {"retryDelay": "40s"}), say(("submit", DECISION)), say(("submit", DECISION))]
    await run()
    await run()
    assert ["test" in path for path, _ in sent] == [True, False, False]


async def test_short_rate_limit_is_waited_out_when_every_other_model_is_used_up(server, monkeypatch):
    turns, sent = server
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    monkeypatch.setenv("MODEL_FALLBACKS", "gemini-second")
    quota = fail(429, {"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]})
    turns += [fail(429, {"retryDelay": "30s"}), quota, say(("submit", DECISION))]
    await run()
    assert 25 < waits[-1] <= 30
    assert ["second" in path for path, _ in sent] == [False, True, False]


async def test_daily_quota_and_bad_requests_fail_at_once(server):
    turns, sent = server
    turns += [fail(429, {"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]})]
    with pytest.raises(llm.AgentFailed, match="daily free quota"):
        await run()
    turns += [fail(400)]
    with pytest.raises(llm.AgentFailed, match="400"):
        await run()
    assert len(sent) == 2


async def test_blocked_answer_and_missing_key_fail(server, monkeypatch):
    turns, _ = server
    turns += [say(reason="SAFETY")]
    with pytest.raises(llm.AgentFailed, match="SAFETY"):
        await run()
    monkeypatch.delenv("GEMINI_API_KEY")
    llm.gemini_client.cache_clear()
    with pytest.raises(llm.AgentFailed, match="no Gemini API key"):
        await run()


async def test_schemas_are_self_contained():
    schema = llm._inline_refs(Finding.model_json_schema())
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    assert schema["properties"]["evidence"]["items"]["properties"]["kind"]["enum"]


async def test_repeated_rate_limits_back_off_then_fail(server, monkeypatch):
    turns, _ = server
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    turns += [fail(429)] * 4
    with pytest.raises(llm.ModelUnavailable):
        await run()
    assert [wait for wait in waits if wait >= 1][:2] == [2, 4]


async def test_claude_takes_over_when_gemini_is_unavailable(server, monkeypatch):
    turns, _ = server
    quota = fail(429, {"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]})
    turns += [quota]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    asked = []

    async def ask(self, usage):
        asked.append(self.request["model"])
        usage["out"] += 1
        return [llm.Call("c", "submit", DECISION)]

    monkeypatch.setattr(llm.ClaudeChat, "ask", ask)
    assert (await run()).remedy == "code_fix"
    assert asked == ["claude-sonnet-5-5"]
    assert any("continue on claude-sonnet-5-5" in event["summary"] for event in bridge.events())


async def test_limiter_holds_rate_concurrency_and_priority(monkeypatch):
    monkeypatch.setenv("PHOENIX_LLM_RPM", "3")
    monkeypatch.setenv("PHOENIX_LLM_CONCURRENCY", "1")
    monkeypatch.setattr(llm, "WINDOW", 0.6)
    limiter, order, running = llm.Limiter(), [], []

    async def call(name, level):
        llm.priority.set(level)
        async with limiter.slot(rated=True):
            order.append((name, time.monotonic()))
            running.append(limiter.running)
            await asyncio.sleep(0.02)

    tasks = [asyncio.create_task(call("first", 0))]
    await asyncio.sleep(0.01)
    tasks += [asyncio.create_task(call(f"judge{n}", llm.LOW_PRIORITY)) for n in range(2)]
    tasks += [asyncio.create_task(call(f"main{n}", 0)) for n in range(2)]
    await asyncio.gather(*tasks)
    names = [name for name, _ in order]
    assert names == ["first", "main0", "main1", "judge0", "judge1"]
    assert max(running) == 1
    assert order[3][1] - order[0][1] >= 0.55


async def test_cancelled_caller_leaves_the_queue(monkeypatch):
    monkeypatch.setenv("PHOENIX_LLM_CONCURRENCY", "1")
    limiter = llm.Limiter()

    async def call():
        async with limiter.slot(rated=False):
            await asyncio.sleep(0.05)

    first, waiting = asyncio.create_task(call()), None
    await asyncio.sleep(0.01)
    waiting = asyncio.create_task(call())
    await asyncio.sleep(0.01)
    waiting.cancel()
    await asyncio.gather(first, waiting, return_exceptions=True)
    assert limiter.queue == [] and limiter.running == 0
    await asyncio.wait_for(call(), 1)
