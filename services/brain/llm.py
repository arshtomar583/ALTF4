"""The agent loop: one model, a fixed tool list, one validated answer."""

import asyncio
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import anthropic
from google import genai
from google.genai import errors as gemini_errors
from google.genai import types as gemini
from pydantic import BaseModel, ValidationError

from .agents import bridge
from .agents.base import clip, note

LLM_TIMEOUT = 60
TOOL_TIMEOUT = 20
AGENT_TIMEOUT = 120
MAX_TOOL_OUTPUT = 4_000
MAX_EXCERPT = 300
MAX_TOKENS = 16_000
MAX_RETRY_DELAY = 45


class AgentFailed(Exception):
    """The agent could not produce a valid answer."""


class AnswerRejected(ValueError):
    """A well-formed answer broke a rule enforced in code."""


class EvidenceMismatch(AnswerRejected):
    """Quoted evidence does not match its source. Re-asked separately from other rules."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class Call:
    id: str | None
    name: str
    input: dict


@dataclass(frozen=True)
class Result:
    call: Call
    text: str
    ok: bool


@lru_cache(maxsize=1)
def client() -> anthropic.AsyncAnthropic:
    # max_retries=1 retries once on 429, 5xx and connection errors.
    return anthropic.AsyncAnthropic(timeout=LLM_TIMEOUT, max_retries=1)


@lru_cache(maxsize=1)
def gemini_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise AgentFailed("no Gemini API key is set")
    # GeminiChat retries itself, so a free-tier 429 can wait as long as the server asks.
    options = gemini.HttpOptions(
        timeout=LLM_TIMEOUT * 1000, retry_options=gemini.HttpRetryOptions(attempts=1)
    )
    return genai.Client(api_key=key, http_options=options)


class ClaudeChat:
    """Append-only: thinking blocks stay valid only while earlier turns are unchanged."""

    def __init__(self, name: str, model: str, system: str, user: str, tools: list[dict]):
        self.name = name
        self.request = {"model": model, "system": system, "tools": tools}
        self.messages: list[dict] = [{"role": "user", "content": user}]

    async def ask(self, usage: dict) -> list[Call]:
        response = await client().messages.create(
            max_tokens=MAX_TOKENS,
            messages=self.messages,
            output_config={"effort": "medium"},
            **self.request,
        )
        usage["in"] += (
            response.usage.input_tokens
            + (response.usage.cache_read_input_tokens or 0)
            + (response.usage.cache_creation_input_tokens or 0)
        )
        usage["out"] += response.usage.output_tokens
        if response.stop_reason in ("refusal", "max_tokens"):
            raise AgentFailed(f"{self.name} stopped early: {response.stop_reason}")
        self.messages.append({"role": "assistant", "content": response.content})
        return [
            Call(block.id, block.name, block.input)
            for block in response.content
            if block.type == "tool_use"
        ]

    def reply(self, results: list[Result], nudge: str | None) -> None:
        content: list[dict] = [
            {
                "type": "tool_result",
                "tool_use_id": result.call.id,
                "content": result.text,
                "is_error": not result.ok,
            }
            for result in results
        ]
        if nudge:
            content.append({"type": "text", "text": nudge})
        self.messages.append({"role": "user", "content": content})


class GeminiChat:
    """Model turns are replayed untouched, because they carry thought signatures."""

    def __init__(self, name: str, model: str, system: str, user: str, tools: list[dict]):
        self.name = name
        self.model = model
        declarations = [
            gemini.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters_json_schema=_inline_refs(tool["input_schema"]),
            )
            for tool in tools
        ]
        self.config = gemini.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_TOKENS,
            tools=[gemini.Tool(function_declarations=declarations)],
            tool_config=gemini.ToolConfig(
                function_calling_config=gemini.FunctionCallingConfig(mode="ANY")
            ),
            thinking_config=gemini.ThinkingConfig(thinking_level="MEDIUM"),
            automatic_function_calling=gemini.AutomaticFunctionCallingConfig(disable=True),
        )
        self.contents = [gemini.Content(role="user", parts=[gemini.Part(text=user)])]

    async def ask(self, usage: dict) -> list[Call]:
        response = await self._generate()
        if counts := response.usage_metadata:
            usage["in"] += counts.prompt_token_count or 0
            usage["out"] += (counts.candidates_token_count or 0) + (counts.thoughts_token_count or 0)
        candidate = response.candidates[0] if response.candidates else None
        parts = (candidate and candidate.content and candidate.content.parts) or []
        calls = [part.function_call for part in parts if part.function_call]
        reason = candidate.finish_reason if candidate else None
        if not calls and reason not in (
            gemini.FinishReason.STOP,
            gemini.FinishReason.MALFORMED_FUNCTION_CALL,
            gemini.FinishReason.UNEXPECTED_TOOL_CALL,
        ):
            raise AgentFailed(f"{self.name} stopped early: {reason.value if reason else 'blocked'}")
        if parts:
            self.contents.append(candidate.content)
        return [Call(call.id, call.name, call.args or {}) for call in calls]

    async def _generate(self) -> gemini.GenerateContentResponse:
        models = gemini_client().aio.models
        for attempt in (1, 2):
            try:
                return await asyncio.wait_for(
                    models.generate_content(
                        model=self.model, contents=self.contents, config=self.config
                    ),
                    LLM_TIMEOUT,
                )
            except gemini_errors.APIError as error:
                detail = f"{error.code} {error.message}"
                details = json.dumps(error.details, default=str)
                daily = "PerDay" in details
                if attempt == 2 or daily or (error.code != 429 and error.code < 500):
                    limit = "daily free quota is used up: " if daily else ""
                    raise AgentFailed(f"{self.name} {limit}{clip(detail, 200)}") from error
                asked = re.search(r'"retryDelay": "([\d.]+)s"', details)
                delay = float(asked[1]) if asked else 2
                await asyncio.sleep(min(delay, MAX_RETRY_DELAY))
            except Exception as error:
                if attempt == 2:
                    reason = f"{self.name} could not reach the model: {type(error).__name__}"
                    raise AgentFailed(reason) from error
        raise AssertionError("unreachable")

    def reply(self, results: list[Result], nudge: str | None) -> None:
        parts = []
        for result in results:
            response = {"output" if result.ok else "error": result.text}
            if nudge:
                # Text parts beside function responses are not portable, so it rides along.
                response["note"] = nudge
            parts.append(
                gemini.Part(
                    function_response=gemini.FunctionResponse(
                        id=result.call.id, name=result.call.name, response=response
                    )
                )
            )
        self.contents.append(
            gemini.Content(role="user", parts=parts or [gemini.Part(text=nudge or "Continue.")])
        )


def _inline_refs(schema: dict) -> dict:
    """Expand $ref, which Gemini function schemas do not document. Models are not recursive."""
    definitions = schema.get("$defs", {})

    def expand(node: Any) -> Any:
        if isinstance(node, list):
            return [expand(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return expand(definitions[node["$ref"].rsplit("/", 1)[-1]])
        return {key: expand(value) for key, value in node.items() if key != "$defs"}

    return expand(schema)


async def run_agent(
    *,
    name: str,
    system: str,
    user: str,
    tools: list[Tool],
    model: str,
    output_model: type[BaseModel],
    incident_id: str,
    max_turns: int = 8,
    check: Callable[[Any], Awaitable[Any]] | None = None,
) -> BaseModel:
    """Run one agent. check returns the accepted answer or raises AnswerRejected to re-ask once."""
    usage = {"in": 0, "out": 0}
    try:
        async with asyncio.timeout(AGENT_TIMEOUT):
            result = await _loop(
                name, system, user, tools, model, output_model, incident_id, max_turns, usage, check
            )
    except TimeoutError as error:
        raise AgentFailed(f"{name} ran out of time after {AGENT_TIMEOUT}s") from error
    except anthropic.AnthropicError as error:
        raise AgentFailed(f"{name} could not reach the model: {error}") from error
    except TypeError as error:
        # The SDK raises TypeError when no credentials are configured.
        if "authentication" not in str(error):
            raise
        raise AgentFailed(f"{name} has no Anthropic API key") from error
    finally:
        if usage["out"]:
            await note(
                incident_id, name, f"Used {usage['in']} tokens in, {usage['out']} out", {"usage": usage}
            )
    return result


async def _loop(
    name: str,
    system: str,
    user: str,
    tools: list[Tool],
    model: str,
    output_model: type[BaseModel],
    incident_id: str,
    max_turns: int,
    usage: dict,
    check: Callable[[Any], Awaitable[Any]] | None,
) -> BaseModel:
    allowed = {tool.name: tool for tool in tools}
    definitions = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
    ]
    definitions.append(
        {
            "name": "submit",
            "description": "Submit your final answer. Call this exactly once, when you are done.",
            "input_schema": output_model.model_json_schema(),
        }
    )
    system += "\n\nWhen you are done, call the submit tool. Plain text is never read as an answer."
    chat_type = GeminiChat if model.startswith("gemini-") else ClaudeChat
    chat = chat_type(name, model, system, user, definitions)
    reasked: set[type] = set()

    turn = -1
    # A re-ask never costs the agent one of its own turns.
    while (turn := turn + 1) < max_turns + len(reasked):
        calls = await chat.ask(usage)
        results: list[Result] = []
        work = []
        for call in calls:
            if call.name != "submit":
                work.append(call)
                continue
            try:
                answer = output_model.model_validate(call.input)
                return await check(answer) if check else answer
            except (ValidationError, AnswerRejected) as error:
                if type(error) in reasked:
                    raise AgentFailed(f"{name} submitted an invalid answer twice: {error}") from error
                reasked.add(type(error))
                results.append(_result(call, {"error": f"Invalid answer, fix and resubmit: {error}"}))

        results += await asyncio.gather(
            *(_run_tool(name, call, allowed.get(call.name), incident_id, usage) for call in work)
        )
        nudge = None
        if not calls:
            nudge = "Call the submit tool with your final answer."
        elif turn == max_turns + len(reasked) - 2:
            nudge = "One turn left. Call submit now."
        chat.reply(results, nudge)

    raise AgentFailed(f"{name} gave no answer in {max_turns} turns")


async def _run_tool(name: str, call: Call, tool: Tool | None, incident_id: str, usage: dict) -> Result:
    if tool is None:
        output: Any = {"error": f"Tool {call.name!r} is not available to you"}
    else:
        try:
            output = await asyncio.wait_for(tool.fn(**call.input), TOOL_TIMEOUT)
        except TimeoutError:
            output = {"error": f"{call.name} timed out after {TOOL_TIMEOUT}s"}
        except Exception as error:
            output = {"error": f"{type(error).__name__}: {error}"}
    ok = not (isinstance(output, dict) and "error" in output)
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    args = {
        key: clip(value, MAX_EXCERPT) if isinstance(value, str) else value
        for key, value in call.input.items()
    }
    label = call.name.replace("_", " ")
    await bridge.emit(
        "agent.tool",
        incident_id=incident_id,
        actor=name,
        summary=f"Runs {label}" if ok else f"Tries {label}, but it fails",
        data={
            "tool": call.name,
            "args": args,
            "ok": ok,
            "result_excerpt": clip(text, MAX_EXCERPT),
            "usage": dict(usage),
        },
    )
    return _result(call, text, ok)


def _result(call: Call, output: Any, ok: bool = False) -> Result:
    text = output if isinstance(output, str) else json.dumps(output)
    return Result(call, clip(text, MAX_TOOL_OUTPUT), ok)
