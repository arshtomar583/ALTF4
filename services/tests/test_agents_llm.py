import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from brain import llm
from brain.agents import bridge

pytestmark = pytest.mark.asyncio
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}}


class Answer(BaseModel):
    total: int


def call(name, **input):
    return SimpleNamespace(type="tool_use", id=f"call-{name}", name=name, input=input)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "phoenix.sqlite3"
    monkeypatch.setenv("PHOENIX_DB_PATH", str(path))
    return path


@pytest.fixture
def scripted(monkeypatch):
    """Replace the API with fixed turns and record the messages sent."""

    def use(*turns):
        queue, sent = list(turns), []

        async def create(**kwargs):
            sent.append(list(kwargs["messages"]))
            return SimpleNamespace(
                content=queue.pop(0),
                stop_reason="tool_use",
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=5,
                    cache_read_input_tokens=None,
                    cache_creation_input_tokens=0,
                ),
            )

        fake = SimpleNamespace(messages=SimpleNamespace(create=create))
        monkeypatch.setattr(llm, "client", lambda: fake)
        return sent

    return use


def events(path):
    return [(event["type"], event | event["data"]) for event in bridge.events()]


def run(tools=(), **extra):
    return llm.run_agent(
        name="dummy", system="s", user="u", tools=list(tools), model="m",
        output_model=Answer, incident_id="inc-1", **extra,
    )


def last_result(sent):
    return sent[-1][-1]["content"][0]


async def test_tools_run_and_answer_is_validated(db, scripted):
    async def echo(text):
        return text * 5000

    sent = scripted([call("echo", text="a")], [call("submit", total=3)])
    answer = await run([llm.Tool("echo", "Echo", SCHEMA, echo)])

    assert answer.total == 3
    assert len(last_result(sent)["content"]) == llm.MAX_TOOL_OUTPUT
    type_, payload = events(db)[0]
    assert type_ == "agent.tool" and payload["ok"] and payload["actor"] == "dummy"
    assert len(payload["result_excerpt"]) <= 300
    usage = events(db)[-1][1]["usage"]
    assert (usage["in"], usage["out"], usage["calls"]) == (20, 10, 2) and usage["seconds"] >= 0


async def test_unlisted_tool_is_rejected(db, scripted):
    sent = scripted([call("rm_rf")], [call("submit", total=1)])
    await run()
    assert "not available" in last_result(sent)["content"]
    assert events(db)[0][1]["ok"] is False


async def test_tool_exception_is_returned_not_raised(db, scripted):
    async def boom():
        raise ValueError("no luck")

    sent = scripted([call("boom")], [call("submit", total=1)])
    await run([llm.Tool("boom", "Fails", SCHEMA, boom)])
    assert json.loads(last_result(sent)["content"]) == {"error": "ValueError: no luck"}
    assert last_result(sent)["is_error"]


async def test_tool_timeout_is_returned(db, scripted, monkeypatch):
    monkeypatch.setattr(llm, "TOOL_TIMEOUT", 0.01)

    async def slow():
        await asyncio.sleep(5)

    sent = scripted([call("slow")], [call("submit", total=1)])
    await run([llm.Tool("slow", "Slow", SCHEMA, slow)])
    assert "timed out" in last_result(sent)["content"]


async def test_invalid_answer_is_reasked_once(db, scripted):
    scripted([call("submit", total="x")], [call("submit", total=2)])
    assert (await run()).total == 2

    scripted([call("submit", total="x")], [call("submit", total="y")])
    with pytest.raises(llm.AgentFailed, match="twice"):
        await run()


async def test_no_answer_and_no_time_fail(db, scripted, monkeypatch):
    scripted([], [])
    with pytest.raises(llm.AgentFailed, match="2 turns"):
        await run(max_turns=2)

    async def hang(**_):
        await asyncio.sleep(5)

    monkeypatch.setattr(llm, "AGENT_TIMEOUT", 0.01)
    monkeypatch.setattr(
        llm, "client", lambda: SimpleNamespace(messages=SimpleNamespace(create=hang))
    )
    with pytest.raises(llm.AgentFailed, match="ran out of time"):
        await run()


async def test_completed_step_is_not_rerun(db):
    runs = []

    async def work():
        runs.append(1)
        return {"n": len(runs)}

    async def fail():
        raise RuntimeError("killed")

    with pytest.raises(RuntimeError):
        await bridge.step("inc-1", "decide", fail)
    assert await bridge.step("inc-1", "decide", work) == {"n": 1}
    assert await bridge.step("inc-1", "decide", work) == {"n": 1}
    assert await bridge.step("inc-2", "decide", work) == {"n": 2}


async def test_events_have_one_shape(db):
    await bridge.emit("agent.note", incident_id="i", actor="a", summary="s", data={"x": 1})
    event = bridge.events()[0]
    fields = {key: event[key] for key in ("type", "incident_id", "actor", "summary", "data")}
    assert fields == {
        "type": "agent.note", "incident_id": "i", "actor": "a", "summary": "s", "data": {"x": 1},
    }
