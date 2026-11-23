"""Helpers shared by every agent."""

import os
import re

from collections.abc import Callable
from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any

from . import bridge
from .models import Evidence, Finding

MAX_SUMMARY = 90
CORRECTED = "Evidence excerpt corrected to the actual line"
MIN_OVERLAP = 12
CLOSE_ENOUGH = 0.6
QUOTES = str.maketrans(dict.fromkeys("‘’“”`\"", "'"))


def fast_model() -> str:
    return os.getenv("MODEL_FAST") or "gemini-3.5-flash-lite"


def strong_model() -> str:
    return os.getenv("MODEL_STRONG") or "gemini-3.8-flash"


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def note(incident_id: str, actor: str, summary: str, data: dict | None = None) -> None:
    await bridge.emit(
        "agent.note",
        incident_id=incident_id,
        actor=actor,
        summary=clip(summary, MAX_SUMMARY),
        data=data,
    )


async def add_finding(incident_id: str, finding: Finding) -> None:
    await bridge.emit(
        "finding.added",
        incident_id=incident_id,
        actor=finding.agent,
        summary=clip(finding.claim, MAX_SUMMARY),
        data=finding.model_dump(),
    )


def observed(tool: Any, record: Callable[[Any], None]) -> Any:
    """Wrap a tool so code can check what the agent really saw."""

    async def fn(**args: Any) -> Any:
        output = await tool.fn(**args)
        record(output)
        return output

    return replace(tool, fn=fn)


def announced(tool: Any, incident_id: str, actor: str, summary: str) -> Any:
    """Wrap a tool so its first use posts one note."""
    said = False

    async def fn(**args: Any) -> Any:
        nonlocal said
        if not said:
            said = True
            await note(incident_id, actor, summary)
        return await tool.fn(**args)

    return replace(tool, fn=fn)


def normalise(line: str) -> str:
    """Ignore whitespace and quote style. A diff sign keeps its meaning but not its indent."""
    text = " ".join(line.translate(QUOTES).split())
    return text[0] + text[1:].strip() if text[:1] in ("+", "-") else text


def line_matches(line: str, real: str) -> bool:
    quoted, actual = normalise(line), normalise(real)
    if quoted == actual:
        return True
    if quoted[:1] in ("+", "-") and actual[:1] in ("+", "-") and quoted[0] != actual[0]:
        return False
    shorter, longer = sorted((quoted, actual), key=len)
    return len(shorter) >= MIN_OVERLAP and shorter in longer


def mismatches(evidence: list[Evidence], real: list[str]) -> list[str]:
    return [line for item in evidence for line in _quoted(item) if not _is_real(line, real)]


def _quoted(item: Evidence) -> list[str]:
    # Lines without a letter or digit ("});", "...") carry no claim to check.
    return [line for line in item.excerpt.splitlines() if re.search(r"\w", line)]


def _is_real(line: str, real: list[str]) -> bool:
    return any(line_matches(line, candidate) for candidate in real)


def closest(line: str, real: list[str]) -> str | None:
    scored = [
        (SequenceMatcher(None, normalise(line), normalise(text), autojunk=False).ratio(), text)
        for text in real
    ]
    score, text = max(scored, default=(0, ""), key=lambda pair: pair[0])
    return text if score >= CLOSE_ENOUGH else None


def correct(item: Evidence, real: list[str]) -> Evidence | None:
    """Swap misquoted lines for the closest real ones. None when nothing real remains."""
    kept = []
    for line in _quoted(item):
        actual = line if _is_real(line, real) else closest(line, real)
        if actual is not None and actual not in kept:
            kept.append(actual)
    return item.model_copy(update={"excerpt": "\n".join(kept)}) if kept else None


def with_question(prompt: str, question: str | None) -> str:
    if not question:
        return prompt
    return (
        f"{prompt}\n\nFollow-up: a colleague's claim is below. It may be wrong and is not evidence. "
        f"Check it with your own tools, then submit your own finding.\nClaim: {question}"
    )
