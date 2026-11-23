"""Turn a restored incident into a verified candidate, or escalate. Never touches live slots."""

import asyncio
import os
from collections.abc import Awaitable, Callable

from pathlib import Path
from typing import Any

from ..lab import Lab, LabError, get_lab
from ..llm import AgentFailed
from . import bridge, code_investigator, coordinator, repair, runtime_investigator, verifier
from .base import note
from .models import (
    Candidate,
    Decision,
    Finding,
    IncidentContext,
    PipelineOutcome,
    Verification,
)

MAX_ATTEMPTS = 3
Investigate = Callable[[Lab, IncidentContext], Awaitable[Finding]]


def context_for(incident: Any) -> IncidentContext:
    """Build the context from the store's incident record."""
    return IncidentContext(
        incident_id=incident.id,
        broken_rev=incident.broken_rev,
        healthy_rev=incident.healthy_rev,
        broken_slot=incident.broken_slot,
        failing_request=incident.failing_request,
        shop_repo=Path(os.getenv("PHOENIX_SHOP_REPO") or ".data/shoplite-repo"),
    )


def current_context() -> IncidentContext | None:
    """The open incident, or the last one this layer worked on."""
    store = bridge.store()
    if hasattr(store, "current_incident") and (incident := store.current_incident()):
        return context_for(incident)
    saved = bridge.latest_step("context")
    return IncidentContext.model_validate(saved) if saved else None


async def observe(ctx: IncidentContext) -> IncidentContext:
    """Reproduce the failure in the lab, which also records the logs lab_logs reads."""
    lab = get_lab(ctx.shop_repo, ctx.healthy_rev)
    await lab.prepare()
    seen = await lab.replay(ctx.broken_rev, ctx.failing_request)
    if ctx.failing_request.status is not None:
        return ctx
    # A shop that never starts reaches customers as a gateway error.
    request = ctx.failing_request.model_copy(
        update={"status": seen["status"] or 502, "response_excerpt": seen["body_excerpt"]}
    )
    return ctx.model_copy(update={"failing_request": request})


async def run_after_restore(ctx: IncidentContext) -> PipelineOutcome:
    async def context() -> dict:
        return ctx.model_dump(mode="json")

    await bridge.step(ctx.incident_id, "context", context)
    findings: dict[str, Finding | None] = {}
    attempts: list[tuple[Candidate, Verification]] = []
    try:
        ctx = await observe(ctx)
        findings = await diagnose(ctx)
        decision = await decide(ctx, findings)
        if decision.remedy == "escalate":
            return PipelineOutcome(status="escalated")
        reason = await _repair_until_verified(ctx, decision, attempts)
    except LabError as error:
        reason = f"The lab could not continue: {str(error)[:200]}"
    if attempts and attempts[-1][1].verdict == "VERIFIED":
        candidate, verification = attempts[-1]
        return PipelineOutcome(
            status="verified",
            candidate_id=candidate.candidate_id,
            candidate_rev=candidate.rev,
            verification=verification,
        )

    async def escalate() -> dict:
        return {"evidence_pack_md": await coordinator.escalate(ctx.incident_id, findings, reason, attempts)}

    await bridge.step(ctx.incident_id, "escalate", escalate)
    return PipelineOutcome(status="escalated")


async def _repair_until_verified(
    ctx: IncidentContext, decision: Decision, attempts: list[tuple[Candidate, Verification]]
) -> str:
    """Fill attempts until one is VERIFIED. Returns why it stopped otherwise."""
    # Restoring config has one possible outcome, so a second attempt cannot differ.
    limit = 1 if decision.remedy == "config_restore" else MAX_ATTEMPTS
    for n in range(1, limit + 1):
        try:
            candidate = await propose(ctx, decision, n, attempts[-1][1] if attempts else None)
        except AgentFailed as error:
            summary = f"Attempt {n} produced no fix"
            await note(ctx.incident_id, repair.NAME, summary, {"error": str(error)[:300]})
            continue
        verification = await verify(ctx, candidate, n)
        attempts.append((candidate, verification))
        if verification.verdict == "VERIFIED":
            return ""
    if not attempts:
        return "The repair agent could not produce a candidate"
    return f"No candidate passed verification: {attempts[-1][1].reason}"


async def propose(
    ctx: IncidentContext, decision: Decision, n: int, previous: Verification | None
) -> Candidate:
    lab = get_lab(ctx.shop_repo, ctx.healthy_rev)

    async def run() -> dict:
        if decision.remedy == "config_restore":
            return (await repair.restore_config(lab, ctx, n)).model_dump()
        return (await repair.repair(lab, ctx, decision, n, previous)).model_dump()

    return Candidate.model_validate(await bridge.step(ctx.incident_id, f"repair.c{n}", run))


async def verify(ctx: IncidentContext, candidate: Candidate, n: int) -> Verification:
    lab = get_lab(ctx.shop_repo, ctx.healthy_rev)

    async def run() -> dict:
        return (await verifier.verify(lab, ctx, candidate.candidate_id, candidate.rev)).model_dump()

    return Verification.model_validate(await bridge.step(ctx.incident_id, f"verify.c{n}", run))


async def diagnose(ctx: IncidentContext) -> dict[str, Finding | None]:
    """Run both investigators at once. Neither receives the other's output."""
    lab = get_lab(ctx.shop_repo, ctx.healthy_rev)
    code, runtime = await asyncio.gather(
        _investigate(ctx, lab, "diagnose.code", code_investigator.NAME, code_investigator.investigate),
        _investigate(
            ctx, lab, "diagnose.runtime", runtime_investigator.NAME, runtime_investigator.investigate
        ),
    )
    return {"code": code, "runtime": runtime}


async def decide(ctx: IncidentContext, findings: dict[str, Finding | None]) -> Decision:
    """Choose the remedy once. A restart reuses the saved choice."""
    investigators = {
        "code": code_investigator.investigate,
        "runtime": runtime_investigator.investigate,
    }

    async def follow_up(key: str, question: str) -> Finding:
        lab = get_lab(ctx.shop_repo, ctx.healthy_rev)
        await lab.prepare()
        return await investigators[key](lab, ctx, question)

    async def run() -> dict:
        decision, pack = await coordinator.decide(ctx.incident_id, findings, follow_up)
        return {"decision": decision.model_dump(), "evidence_pack_md": pack}

    saved = await bridge.step(ctx.incident_id, "decide", run)
    return Decision.model_validate(saved["decision"])


async def _investigate(
    ctx: IncidentContext, lab: Lab, key: str, name: str, investigate: Investigate
) -> Finding | None:
    async def run() -> dict:
        return (await investigate(lab, ctx)).model_dump()

    try:
        # A failed investigation is not checkpointed, so a restart tries it again.
        return Finding.model_validate(await bridge.step(ctx.incident_id, key, run))
    except AgentFailed as error:
        await note(ctx.incident_id, name, "I could not reach a finding", {"error": str(error)[:300]})
        return None
