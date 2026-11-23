"""Run the AI layer end to end on a throwaway incident.

No flag       the whole pipeline, a bad candidate and the report; --resume continues a run that was killed
--only dummy  one live two-tool agent (needs a model key)
--only lab    the same checkout on the healthy and broken revisions
--only diagnose  both investigators in parallel (needs a model key)
--only decide    the coordinator, on the findings the last run saved
--only repair    one repair attempt, on the decision the last run saved
--only verify    the verifier, on the candidate the last run saved

All modes except dummy and lab share .data/dev-incident; those two use a temp folder.
"""

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Coroutine
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from pydantic import BaseModel  # noqa: E402

from brain import event_store, llm, report  # noqa: E402
from brain.agents import bridge  # noqa: E402
from brain.agents.base import fast_model  # noqa: E402
from brain.agents.models import (  # noqa: E402
    Candidate,
    Decision,
    FailingRequest,
    Finding,
    IncidentContext,
    Verification,
)
from brain.agents import pipeline  # noqa: E402
from brain.agents.pipeline import decide, diagnose  # noqa: E402
from brain.agents.router import challenge  # noqa: E402
from brain.lab import Lab, get_lab  # noqa: E402

SAVED = ROOT / ".data" / "dev-incident"
SHOPLITE = ROOT / "services" / "shoplite"
BAD_COMMIT = ROOT / "faults" / "bad_commit"
CHECKOUT = {"sku": "demo-mug", "quantity": 1}


class Stop(Exception):
    """End the run with a plain message."""


def git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.name=Shop Dev", "-c", "user.email=dev@shop.test"]
    command = ["git", *identity, *args]
    return subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise Stop(f"{path.name} changed and no longer contains: {old}")
    path.write_text(text.replace(old, new))


def build_shop_repo(repo: Path, folder: str = "services/shoplite") -> dict[str, str]:
    """Create a history like the team repo's, with a healthy tag and a checkout-breaking commit."""
    ignore = shutil.ignore_patterns("node_modules", ".env*", "*.log")
    root = repo / folder
    shutil.copytree(SHOPLITE, root, ignore=ignore)
    # The synthetic history should show the faulty line even from a CRLF checkout.
    (repo / ".gitattributes").write_text("*.js text eol=lf\n")
    (repo / ".gitignore").write_text("node_modules\n")
    git(repo, "init", "-q", "-b", "main")
    healthy = commit(repo, "ShopLite checkout")
    shop = root / "src" / "shop.js"

    if BAD_COMMIT.is_dir():
        git(repo, "tag", "healthy")
        shutil.copytree(BAD_COMMIT, root, dirs_exist_ok=True, ignore=ignore)
        broken = commit(repo, "Update checkout")
        return {"healthy": healthy, "culprit": broken, "broken": broken}

    git(repo, "tag", "healthy")

    readme = root / "README.md"
    readme.write_text("# ShopLite\n\nCheckout service.\n")
    commit(repo, "Add a README")
    replace(shop, '{ limit: "8kb" }', '{ limit: "16kb" }')
    commit(repo, "Allow larger carts")
    replace(shop, "request.body?.discount?.code", "request.body.discount.code.trim().toUpperCase()")
    culprit = commit(repo, "Accept lowercase discount codes")
    readme.write_text(readme.read_text() + "\nRun the tests with npm test.\n")
    broken = commit(repo, "Document the tests")
    return {"healthy": healthy, "culprit": culprit, "broken": broken}


async def build_context(folder: Path) -> tuple[Lab, IncidentContext]:
    repo = folder / "shop"
    revs = build_shop_repo(repo)
    lab = get_lab(repo, revs["healthy"])
    await lab.prepare()
    context = IncidentContext(
        incident_id="inc-dev",
        broken_rev=revs["broken"],
        healthy_rev=revs["healthy"],
        broken_slot="a",
        failing_request=FailingRequest(body=CHECKOUT),
        shop_repo=repo,
    )
    context = await pipeline.observe(context)
    if context.failing_request.status < 500:
        raise Stop(f"The broken revision answered {context.failing_request.status}, expected a 5xx")
    return lab, context


class CartAnswer(BaseModel):
    total_cents: int
    explanation: str


def last_event() -> int:
    if not bridge.db_path().exists():
        return 0
    return event_store.EventStore(bridge.db_path()).latest_id()


def print_events(after: int) -> int:
    """Print events newer than after, and return the last id printed."""
    if not bridge.db_path().exists():
        return after
    for event in bridge.events():
        if event["id"] > after:
            after = event["id"]
            data = json.dumps(event["data"], ensure_ascii=False)
            print(f"{event['type']:18} {event['actor']}: {event['summary']} {data}", flush=True)
    return after


async def run_live(work: Coroutine, after: int) -> None:
    """Print every event as it happens while the work runs."""
    task = asyncio.create_task(work)
    while not task.done():
        await asyncio.wait({task}, timeout=0.3)
        if not task.done():
            after = print_events(after)
    print_events(after)
    await task


async def dummy() -> None:
    async def get_price(sku: str) -> dict:
        return {"sku": sku, "unit_price_cents": 1200}

    async def get_cart() -> dict:
        return {"sku": "demo-mug", "quantity": 3}

    answer = await llm.run_agent(
        name="dummy",
        system="You total shopping carts. Use your tools; never guess a price.",
        user="What is the cart total?",
        tools=[
            llm.Tool("get_cart", "Return the cart.", {"type": "object", "properties": {}}, get_cart),
            llm.Tool(
                "get_price",
                "Return the unit price of a SKU.",
                {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]},
                get_price,
            ),
        ],
        model=fast_model(),
        output_model=CartAnswer,
        incident_id="inc-dummy",
    )
    print("ANSWER", answer.model_dump_json())


async def show_lab(folder: Path) -> None:
    lab, context = await build_context(folder)
    for name, rev in (("healthy", context.healthy_rev), ("broken", context.broken_rev)):
        result = await lab.replay(rev, context.failing_request)
        print(f"{name:8} {rev[:12]} -> {result['status']} {result['body_excerpt'][:120]}")


def print_finding(title: str, finding: Finding | None) -> None:
    print(f"\n=== {title} ===")
    if finding is None:
        print("No finding; see the agent.note events")
        return
    print(f"Claim       {finding.claim}")
    print(f"Confidence  {finding.confidence:.2f}")
    print(f"Suspect     {finding.suspect_type} {finding.suspect_ref or ''}")
    for item in finding.evidence:
        print(f"[{item.kind}] {item.ref or ''} {item.url or ''}")
        print("\n".join(f"    {line}" for line in item.excerpt.splitlines()))


async def show_diagnosis(folder: Path) -> None:
    _, context = await build_context(folder)
    (folder / "context.json").write_text(context.model_dump_json())
    findings = await diagnose(await pipeline.observe(context))
    print_finding("A: Code Investigator", findings["code"])
    print_finding("B: Runtime Investigator", findings["runtime"])
    print()
    if None in findings.values():
        raise Stop("FAILED an investigator gave no finding")


def saved(incident_id: str, key: str) -> dict | None:
    return bridge.saved_step(incident_id, key)


def forget(key: str) -> None:
    """Run this step afresh, even though it was saved."""
    with closing(sqlite3.connect(bridge.db_path())) as db, db:
        db.execute("DELETE FROM agent_steps WHERE key = ?", (key,))


def load_context(folder: Path) -> IncidentContext:
    path = folder / "context.json"
    if not path.exists():
        raise Stop("Nothing saved yet; run without --only, or --only diagnose, first")
    return IncidentContext.model_validate_json(path.read_text())


def print_candidate(candidate: Candidate) -> None:
    print(f"\n=== Candidate {candidate.candidate_id} ({candidate.rev[:12]}) ===\n{candidate.diff}")


def print_verification(result: Verification) -> None:
    mark = {"pass": "✓", "fail": "✗"}
    checks = ("broken", "healthy", "candidate", "unit_tests")
    print(f"\n=== {result.verdict} ===")
    print("  ".join(f"{name} {mark[getattr(result, name)]}" for name in checks))
    print(f"Diff review {'✓' if result.diff_review_ok else '✗'}\nReason      {result.reason}")
    for issue in result.issues:
        print(f"  - {issue}")


async def record_outage(lab: Lab, context: IncidentContext) -> None:
    """Stand in for the incident engine: real lab checkouts, recorded as it records them."""

    async def probe(rev: str) -> bool:
        started = time.perf_counter()
        result = await lab.replay(rev, context.failing_request)
        bridge.store().record_probe(
            event_store.CheckoutProbe(
                ok=result["status"] == 200,
                status_code=result["status"] or None,
                latency_ms=round((time.perf_counter() - started) * 1000),
                slot=context.broken_slot,
                revision=rev,
            )
        )
        return result["status"] == 200

    async def announce(type_: str, summary: str) -> None:
        await bridge.emit(type_, incident_id=context.incident_id, actor="dev_harness", summary=summary)

    await announce("incident.opened", "Checkout fails in the lab")
    for _ in range(3):
        await probe(context.broken_rev)
    if not await probe(context.healthy_rev):
        raise Stop("Checkout fails on the healthy revision too")
    await announce("incident.restored", "Checkout passes on the healthy revision")


async def show_pipeline(folder: Path) -> None:
    if (folder / "context.json").exists():
        context = load_context(folder)
        print("Resuming the saved incident")
    else:
        lab, context = await build_context(folder)
        (folder / "context.json").write_text(context.model_dump_json())
        await record_outage(lab, context)
    outcome = await pipeline.run_after_restore(context)
    print(f"\n=== Outcome: {outcome.status} {outcome.candidate_rev or ''} ===")
    if outcome.verification:
        print_verification(outcome.verification)
    candidate, verification = await challenge(context)
    print_candidate(candidate)
    print_verification(verification)
    print("\n" + await report.build_report(context.incident_id))


async def show_repair(folder: Path) -> None:
    context = load_context(folder)
    step = saved(context.incident_id, "decide")
    if step is None:
        raise Stop("No saved decision; run --only decide first")
    forget("repair.c1")
    decision = Decision.model_validate(step["decision"])
    print_candidate(await pipeline.propose(context, decision, 1, None))


async def show_verify(folder: Path) -> None:
    context = load_context(folder)
    step = saved(context.incident_id, "repair.c1")
    if step is None:
        raise Stop("No saved candidate; run --only repair first")
    forget("verify.c1")
    candidate = Candidate.model_validate(step)
    print_candidate(candidate)
    print_verification(await pipeline.verify(context, candidate, 1))


async def show_decision(folder: Path) -> None:
    context = load_context(folder)
    findings = {}
    for key in ("code", "runtime"):
        step = saved(context.incident_id, f"diagnose.{key}")
        findings[key] = Finding.model_validate(step) if step else None
    if not any(findings.values()):
        raise Stop("The last diagnose run saved no findings; run --only diagnose again")
    forget("decide")
    decision = await decide(context, findings)
    print(f"\n=== Decision ===\nRemedy      {decision.remedy}\nCause       {decision.cause_type}")
    print(f"Suspect     {decision.suspect_ref}\nReasoning   {decision.reasoning}\n")


MODES = {
    "dummy": lambda folder: dummy(),
    "lab": show_lab,
    "diagnose": show_diagnosis,
    "decide": show_decision,
    "repair": show_repair,
    "verify": show_verify,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--only", choices=list(MODES))
    parser.add_argument("--resume", action="store_true", help="continue the last full run")
    parser.add_argument("--keep", action="store_true", help="keep the temp folder")
    args = parser.parse_args()
    if args.only not in ("dummy", "lab"):
        folder, keep = SAVED, True
        if args.only == "diagnose" or (args.only is None and not args.resume):
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
    else:
        folder, keep = Path(tempfile.mkdtemp(prefix="phoenix-dev-")), args.keep
    os.environ["PHOENIX_DB_PATH"] = str(folder / "phoenix.sqlite3")
    os.environ["PHOENIX_LAB_DIR"] = str(folder / "lab")
    os.environ["PHOENIX_LOG_DIR"] = str(folder / "logs")
    start = last_event()
    try:
        mode = MODES[args.only] if args.only else show_pipeline
        asyncio.run(run_live(mode(folder), start))
    except Stop as error:
        sys.exit(str(error))
    except llm.AgentFailed as error:
        sys.exit(f"FAILED {error}")
    finally:
        if keep:
            print(f"Kept {folder}")
        else:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
