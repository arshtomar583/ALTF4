"""Judge actions and the report. Mount with app.include_router(agents_router)."""

import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .. import report
from ..lab import Lab, LabError, get_lab
from . import bridge, repair, verifier
from .base import note
from .models import Candidate, IncidentContext, Verification
from .pipeline import current_context

ACTOR = "chaos"
agents_router = APIRouter()
_running: set[asyncio.Task] = set()


def spoil(shop: Path) -> str:
    """Swallow the checkout error and charge nothing, or else delete the test's assertions."""
    swallow = r"(() => { try { return \2; } catch { return 0; } })()"
    for source in sorted((shop / "src").rglob("*.js")):
        text = source.read_text()
        for pattern in (
            r"(\btotalCents:\s*)(.+?)(,?)$",
            r"(\b(?:const|let)\s+totalCents\s*=\s*)(.+?)(;)$",
        ):
            spoiled, count = re.subn(pattern, rf"\1{swallow}\3", text, count=1, flags=re.M)
            if count:
                source.write_text(spoiled)
                return "Swallows the checkout error and charges nothing"
    for test in sorted((shop / "test").rglob("*.js")):
        kept = [line for line in test.read_text().splitlines() if not re.match(r"\s*assert\b.*;\s*$", line)]
        test.write_text("\n".join(kept) + "\n")
    return "Deletes the assertions that catch the failure"


async def bad_candidate(lab: Lab, ctx: IncidentContext) -> Candidate:
    earlier = [
        event
        for event in bridge.events()
        if event["incident_id"] == ctx.incident_id
        and event["type"] == "candidate.proposed"
        and event["data"].get("by") == "judge"
    ]
    n = f"judge{len(earlier) + 1}"
    shop, _ = await lab.candidate_branch(ctx.broken_rev, ctx.incident_id, n)
    rationale = spoil(shop)
    rev = await lab.commit(shop, rationale)
    candidate = Candidate(
        candidate_id=f"{ctx.incident_id}-{n}",
        rev=rev,
        diff=await lab.git("diff", await lab.resolve(ctx.broken_rev), rev),
        rationale=rationale,
        by="judge",
    )
    return await repair.propose(
        candidate, ctx.incident_id, ACTOR, "The judge submits a flawed fix for verification"
    )


async def challenge(ctx: IncidentContext) -> tuple[Candidate, Verification]:
    """Propose a flawed candidate and verify it like any other."""
    lab = get_lab(ctx.shop_repo, ctx.healthy_rev)
    await lab.prepare()
    candidate = await bad_candidate(lab, ctx)
    return candidate, await verifier.verify(lab, ctx, candidate.candidate_id, candidate.rev)


async def _verify(lab: Lab, ctx: IncidentContext, candidate: Candidate) -> None:
    try:
        await verifier.verify(lab, ctx, candidate.candidate_id, candidate.rev)
    except Exception as error:
        # The task has no caller, so a failure must be visible as an event.
        await note(
            ctx.incident_id,
            verifier.NAME,
            "I could not check the judge's fix, so it stays unverified",
            {"error": f"{type(error).__name__}: {error}"[:300]},
        )


@agents_router.post("/chaos/bad-candidate", status_code=202)
async def post_bad_candidate() -> dict[str, str]:
    ctx = current_context()
    if ctx is None:
        raise HTTPException(status_code=404, detail="There is no incident to propose a fix for")
    if _running:
        raise HTTPException(status_code=409, detail="A bad candidate is already being verified")
    lab = get_lab(ctx.shop_repo, ctx.healthy_rev)

    async def propose() -> Candidate:
        await lab.prepare()
        return await bad_candidate(lab, ctx)

    task = asyncio.create_task(propose())
    _track(task)
    try:
        candidate = await task
    except LabError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    # Verification takes a minute or two; its result arrives as verification.done.
    _track(asyncio.create_task(_verify(lab, ctx, candidate)))
    return {"status": "verifying", "candidate_id": candidate.candidate_id, "rev": candidate.rev}


def _track(task: asyncio.Task) -> None:
    _running.add(task)
    task.add_done_callback(_running.discard)


@agents_router.get("/incidents/{incident_id}/report", response_class=PlainTextResponse)
async def get_report(incident_id: str) -> PlainTextResponse:
    try:
        markdown = report.render(incident_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return PlainTextResponse(markdown, media_type="text/markdown")
