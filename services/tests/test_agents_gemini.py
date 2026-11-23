"""Drive the real Gemini SDK against a local server, so the wire format is checked."""

import json
import threading
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
    assert waits == [7]

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
