"""The agent loop: one model, a fixed tool list, one validated answer."""

import asyncio
import heapq
import itertools
import json
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
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
# Includes waiting for model quota.
AGENT_TIMEOUT = 180
MAX_TOOL_OUTPUT = 4_000
MAX_EXCERPT = 300
MAX_TOKENS = 16_000
MAX_RETRY_DELAY = 45
QUOTA_TRIES = 3
WINDOW = 60.0
LOW_PRIORITY = 1
CLAUDE_FAST = "claude-haiku-5-5"
CLAUDE_STRONG = "claude-sonnet-5-5"
FAST_AGENTS = ("code_investigator", "runtime_investigator")
FAILOVER_TIMEOUT = 20
BUSY_SECONDS = 600
# These two allow only 20 free requests a day each, so they are a reserve.
DEFAULT_FALLBACKS = "gemini-3.8-flash,gemini-3.6-flash"


class AgentFailed(Exception):
    """The agent could not produce a valid answer."""


class ModelUnavailable(AgentFailed):
    """No model of this provider could answer, so another provider may be tried."""


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


priority: ContextVar[int] = ContextVar("llm_priority", default=0)


class Limiter:
    """One gate for every agent. Callers with a lower priority number always go first.

    The rate is a sliding window, not a refilling bucket: a bucket allows a full burst plus its
    refill inside one minute, which is what a per-minute quota rejects.
    """

    def __init__(self) -> None:
        self.running = 0
        self.started: list[float] = []
        self.queue: list[tuple[int, int]] = []
        self.tickets = itertools.count()

    @asynccontextmanager
    async def slot(self, rated: bool) -> AsyncIterator[None]:
        ticket = (priority.get(), next(self.tickets))
        heapq.heappush(self.queue, ticket)
        try:
            while not self._may_start(ticket, rated):
                await asyncio.sleep(0.05)
        except BaseException:
            self.queue.remove(ticket)
            heapq.heapify(self.queue)
            raise
        heapq.heappop(self.queue)
        self.running += 1
        if rated:
            self.started.append(time.monotonic())
        try:
            yield
        finally:
            self.running -= 1

    def _may_start(self, ticket: tuple[int, int], rated: bool) -> bool:
        oldest = time.monotonic() - WINDOW
        self.started = [moment for moment in self.started if moment > oldest]
        if self.queue[0] != ticket or self.running >= _setting("PHOENIX_LLM_CONCURRENCY", 2):
            return False
        return not rated or len(self.started) < _setting("PHOENIX_LLM_RPM", 12)


def _setting(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name) or default))
    except ValueError:
        return default


limiter = Limiter()


@lru_cache(maxsize=1)
def client() -> anthropic.AsyncAnthropic:
    # max_retries=1 retries once on 429, 5xx and connection errors.
    return anthropic.AsyncAnthropic(timeout=LLM_TIMEOUT, max_retries=1)


@lru_cache(maxsize=1)
def gemini_client() -> genai.Client:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ModelUnavailable("no Gemini API key is set")
    # GeminiChat retries itself, so a free-tier 429 can wait as long as the server asks.
    options = gemini.HttpOptions(
        timeout=LLM_TIMEOUT * 1000, retry_options=gemini.HttpRetryOptions(attempts=1)
    )
    return genai.Client(api_key=key, http_options=options)


class ClaudeChat:
    """Append-only: thinking blocks stay valid only while earlier turns are unchanged."""

    def __init__(
        self, name: str, model: str, system: str, user: str, tools: list[dict], incident_id: str
    ):
        self.name = name
        self.request = {"model": model, "system": system, "tools": tools}
        self.messages: list[dict] = [{"role": "user", "content": user}]

    async def ask(self, usage: dict) -> list[Call]:
        async with limiter.slot(rated=False):
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


_busy_until: dict[str, float] = {}


class _Unavailable(Exception):
    """This model cannot answer now; another one might."""

    def __init__(self, reason: str, seconds: float):
        super().__init__(reason)
        self.seconds = seconds


class GeminiChat:
    """Model turns are replayed untouched, because they carry thought signatures."""

    def __init__(
        self, name: str, model: str, system: str, user: str, tools: list[dict], incident_id: str
    ):
        self.name = name
        self.incident_id = incident_id
        self.model = model
        names = os.getenv("MODEL_FALLBACKS", DEFAULT_FALLBACKS).split(",")
        self.fallbacks = [name.strip() for name in names if name.strip() not in ("", model)]
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
        planned = self.model
        response = await self._generate()
        if self.model != planned:
            # Visible in the events, so nobody mistakes which model answered.
            usage["model"] = self.model
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
        """Retry each model once. A busy or used-up model hands over to the next fallback."""
        skipped: dict[str, float] = {}
        problems: list[str] = []
        while True:
            free_at = _busy_until.get(self.model, 0)
            if time.monotonic() >= free_at:
                try:
                    return await self._generate_once_or_twice()
                except _Unavailable as error:
                    problems.append(str(error))
                    # Other agents skip this model for a while instead of finding out themselves.
                    free_at = time.monotonic() + error.seconds
                    _busy_until[self.model] = free_at
            skipped[self.model] = free_at
            if self.fallbacks:
                self.model = self.fallbacks.pop(0)
                continue
            # Every model was tried. A short rate limit is worth waiting out, once.
            model, free_at = min(skipped.items(), key=lambda item: item[1])
            wait = free_at - time.monotonic()
            if wait > MAX_RETRY_DELAY and problems:
                raise ModelUnavailable(f"{self.name} {'; '.join(problems)[-400:]}")
            # With every model marked busy earlier, the mark may be stale, so ask rather than give up.
            await asyncio.sleep(max(0, wait) if wait <= MAX_RETRY_DELAY else 0)
            self.model = model
            try:
                return await self._generate_once_or_twice()
            except _Unavailable as error:
                _busy_until[model] = time.monotonic() + error.seconds
                raise ModelUnavailable(f"{self.name} {error}") from error

    async def _generate_once_or_twice(self) -> gemini.GenerateContentResponse:
        models = gemini_client().aio.models
        for attempt in range(1, QUOTA_TRIES + 1):
            try:
                async with limiter.slot(rated=True):
                    return await asyncio.wait_for(
                        models.generate_content(
                            model=self.model, contents=self.contents, config=self.config
                        ),
                        FAILOVER_TIMEOUT if self.fallbacks else LLM_TIMEOUT,
                    )
            except gemini_errors.APIError as error:
                detail = f"{self.model} {error.code} {clip(error.message or '', 200)}"
                if error.code != 429 and error.code < 500:
                    raise AgentFailed(f"{self.name} {detail}") from error
                details = json.dumps(error.details, default=str)
                if "PerDay" in details:
                    raise _Unavailable(f"daily free quota is used up: {detail}", 3600) from error
                asked = re.search(r'"retryDelay": "([\d.]+)s"', details)
                delay = float(asked[1]) if asked else 2.0**attempt
                # Quota recovers on its own; an overloaded model is only worth one more try.
                tries = QUOTA_TRIES if error.code == 429 else 2
                # Waiting out a long rate limit costs more than moving to another model.
                if attempt >= tries or (self.fallbacks and delay > 20):
                    raise _Unavailable(detail, delay if error.code == 429 else BUSY_SECONDS) from error
                delay = min(delay, MAX_RETRY_DELAY)
                reason = "quota" if error.code == 429 else "capacity"
                await note(self.incident_id, self.name, f"Waiting for model {reason} ({delay:.0f}s)…")
                await asyncio.sleep(delay)
            except Exception as error:
                if attempt >= 2 or self.fallbacks:
                    reason = f"could not reach {self.model}: {type(error).__name__}"
                    raise _Unavailable(reason, BUSY_SECONDS) from error
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
    usage = {"in": 0, "out": 0, "calls": 0}
    started = time.monotonic()

    async def attempt(model: str) -> BaseModel:
        async with asyncio.timeout(AGENT_TIMEOUT):
            return await _loop(
                name, system, user, tools, model, output_model, incident_id, max_turns, usage, check
            )

    try:
        try:
            result = await attempt(model)
        except ModelUnavailable:
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise
            backup = CLAUDE_FAST if name in FAST_AGENTS else CLAUDE_STRONG
            await note(incident_id, name, f"The model is unavailable, so I continue on {backup}")
            usage["model"] = backup
            result = await attempt(backup)
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
            switched = f" on {usage['model']}" if "model" in usage else ""
            usage["seconds"] = round(time.monotonic() - started, 1)
            calls = "1 model call" if usage["calls"] == 1 else f"{usage['calls']} model calls"
            summary = f"Took {usage['seconds']}s and {calls}{switched}"
            await note(incident_id, name, summary, {"usage": usage})
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
    chat = chat_type(name, model, system, user, definitions, incident_id)
    reasked: set[type] = set()

    turn = -1
    # A re-ask never costs the agent one of its own turns.
    while (turn := turn + 1) < max_turns + len(reasked):
        usage["calls"] += 1
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
