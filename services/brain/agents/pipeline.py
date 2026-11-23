"""Turn a restored incident into a verified candidate, or escalate. Never touches live slots."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from .. import report
from ..event_store import IncidentRecord
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
logger = logging.getLogger(__name__)
_active: set[str] = set()
Investigate = Callable[[Lab, IncidentContext], Awaitable[Finding]]


def context_for(incident: IncidentRecord) -> IncidentContext:
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
    if incident := bridge.store().current_incident():
        return context_for(incident)
    saved = bridge.latest_step("context")
    return IncidentContext.model_validate(saved) if saved else None


async def start_for_incident(incident: IncidentRecord) -> None:
    """Run the pipeline once for a restored incident. Saved steps make a second start resume."""
    if incident.id in _active or bridge.saved_step(incident.id, "outcome") is not None:
        return
    _active.add(incident.id)
    try:
        outcome = await run_after_restore(context_for(incident))

        async def finished() -> dict:
            return outcome.model_dump()

        await bridge.step(incident.id, "outcome", finished)
        if outcome.status == "escalated":
            close_escalated(incident.id)
    except Exception as error:
        # Nothing awaits this task, so a crash must be visible as an event.
        logger.exception("The agent pipeline stopped for %s", incident.id)
        await note(
            incident.id,
            coordinator.NAME,
            "The agents hit an unexpected problem; checkout stays on the healthy version",
            {"error": f"{type(error).__name__}: {error}"[:300]},
        )
    finally:
        _active.discard(incident.id)


def close_escalated(incident_id: str) -> None:
    for event in reversed(bridge.events()):
        if event["incident_id"] == incident_id and event["type"] == "incident.restored":
            data = event["data"]
            bridge.store().close_escalated(
                incident_id, active_slot=data["active_slot"], active_rev=data["active_rev"]
            )
            return


def is_verified(incident_id: str) -> bool:
    return any(
        event["incident_id"] == incident_id
        and event["type"] == "verification.done"
        and event["data"]["verdict"] == "VERIFIED"
        for event in bridge.events()
    )


def is_busy(incident_id: str) -> bool:
    """The pipeline still needs the model quota before anything else may use it."""
    return incident_id in _active and not is_verified(incident_id)


async def publish_report(incident_id: str) -> None:
    async def publish() -> dict:
        await report.build_report(incident_id)
        return {"published": True}

    try:
        await bridge.step(incident_id, "report", publish)
    except Exception:
        logger.exception("The report for %s could not be built", incident_id)


async def report_release(incident: IncidentRecord) -> None:
    await publish_report(incident.id)


async def resume() -> None:
    """Pick up whatever a restarted brain left unfinished."""
    for event in bridge.events():
        released = event["type"] == "release.done" and event["data"].get("ok")
        if released and not bridge.saved_step(event["incident_id"], "report"):
            await publish_report(event["incident_id"])
    incident = bridge.store().current_incident()
    if incident is None or incident.status != "restored":
        return
    if outcome := bridge.saved_step(incident.id, "outcome"):
        if outcome["status"] == "escalated":
            close_escalated(incident.id)
        return
    last = bridge.last_step(incident.id)
    await bridge.emit(
        "system.recovered",
        incident_id=incident.id,
        actor="system",
        summary=f"Back after a restart, continuing after {last}" if last else "Back after a restart",
        data={"last_step": last},
    )
    await start_for_incident(incident)


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

    candidate = Candidate.model_validate(await bridge.step(ctx.incident_id, f"repair.c{n}", run))

    # Announced only once the commit is saved: a restart must never reuse this ID for another commit.
    async def announce() -> dict:
        summary = f"Proposes fix {n} for verification"
        await repair.announce(candidate, ctx.incident_id, repair.NAME, summary)
        return {"announced": candidate.rev}

    await bridge.step(ctx.incident_id, f"proposed.c{n}", announce)
    return candidate


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

    # Once the decision is saved, a finding that was missing can no longer change it.
    if bridge.saved_step(ctx.incident_id, "decide") and not bridge.saved_step(ctx.incident_id, key):
        return None
    try:
        # A failed investigation is not checkpointed, so a restart tries it again.
        return Finding.model_validate(await bridge.step(ctx.incident_id, key, run))
    except AgentFailed as error:
        await note(ctx.incident_id, name, "I could not reach a finding", {"error": str(error)[:300]})
        return None
