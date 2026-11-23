"""Find the commit that broke checkout, from the code history alone."""

import re
from dataclasses import replace

from ..lab import Lab, LabError
from ..llm import AnswerRejected, EvidenceMismatch, Tool, run_agent
from ..tools.git_tools import commits_between, git_tools
from .base import (
    CORRECTED,
    add_finding,
    announced,
    correct,
    fast_model,
    line_matches,
    mismatches,
    note,
    observed,
    with_question,
)
from .models import Finding, IncidentContext

NAME = "code_investigator"
SYSTEM = """You are the Code Investigator on an incident team. Checkout broke between two revisions.
You see only the code history. You have no logs and no web access.

First call git_bisect with good = the healthy revision and bad = the broken revision. It replays the
real failing request, so trust the commit it returns. Then read that commit's diff and explain in
plain English which changed line breaks the failing request, and why.

Submit a finding:
- claim: one or two short sentences a shop owner understands.
- confidence: 0 to 1, how sure the evidence makes you.
- suspect_ref: the full SHA of the first bad commit.
- suspect_type: what kind of change caused it.
- evidence: at least one item with kind "diff", ref "file:line" (line number in the bad commit), and
  excerpt = the exact changed lines copied from the diff, each keeping its leading + or -.
Never guess beyond what the tools returned."""


def build_tools(lab: Lab, ctx: IncidentContext) -> list[Tool]:
    return git_tools(lab, ctx.failing_request, ctx.broken_rev)


async def check_finding(finding: Finding, lab: Lab, bisected: list[dict]) -> None:
    """The rules a code finding must satisfy, whatever the model claims."""
    problems = []
    if not finding.evidence:
        problems.append("evidence is empty")
    elif not any(item.kind == "diff" and item.excerpt.strip() for item in finding.evidence):
        problems.append('evidence needs a kind="diff" item')
    if not bisected:
        problems.append("git_bisect has not returned a result yet; run it first")
    if not re.fullmatch(r"[0-9a-f]{7,40}", finding.suspect_ref or ""):
        problems.append("suspect_ref must be a commit SHA")
    else:
        try:
            await lab.resolve(finding.suspect_ref)
        except LabError:
            problems.append(f"{finding.suspect_ref} is not a commit in this repository")
    if problems:
        raise AnswerRejected("; ".join(problems))


async def changed_lines(lab: Lab, rev: str) -> dict[str, str]:
    """Map every changed line of a commit to its file:line."""
    lines: dict[str, str] = {}
    path, old, new, in_hunk = "", 0, 0, False
    for line in (await lab.show(rev, "--format=")).splitlines():
        if line.startswith("diff --git"):
            path, in_hunk = line.rsplit(" b/", 1)[-1], False
        elif hunk := re.match(r"@@ -(\d+)\S* \+(\d+)", line):
            old, new, in_hunk = int(hunk[1]), int(hunk[2]), True
        elif in_hunk and line.startswith("+"):
            lines.setdefault(line, f"{path}:{new}")
            new += 1
        elif in_hunk and line.startswith("-"):
            lines.setdefault(line, f"{path}:{old}")
            old += 1
        elif in_hunk and not line.startswith("\\"):
            old, new = old + 1, new + 1
    return lines


async def check_evidence(finding: Finding, lab: Lab, final: bool) -> tuple[Finding, bool]:
    """Match diff quotes to the commit. On the final attempt, correct them instead of rejecting."""
    changed = await changed_lines(lab, finding.suspect_ref)
    patch = (await lab.show(finding.suspect_ref, "--format=")).splitlines()
    diffs = [item for item in finding.evidence if item.kind == "diff"]
    quotes_change = any(
        line_matches(line, real)
        for item in diffs
        for line in item.excerpt.splitlines()
        for real in changed
    )
    wrong = mismatches(diffs, patch)
    corrected = bool(wrong) or not quotes_change
    if corrected and not final:
        real = "\n".join(list(changed)[:40])
        raise EvidenceMismatch(
            f"These diff lines are not in the commit: {wrong}. Copy from its real changed lines:\n{real}"
        )
    evidence = []
    for item in finding.evidence:
        if item.kind == "diff":
            item = correct(item, patch if quotes_change else list(changed))
            if item is None:
                continue
            refs = [
                ref
                for line in item.excerpt.splitlines()
                for real, ref in changed.items()
                if line_matches(line, real)
            ]
            # The line number comes from the diff itself, never from the model.
            if refs:
                item = item.model_copy(update={"ref": refs[-1]})
        evidence.append(item)
    if not any(
        line_matches(line, real)
        for item in evidence
        if item.kind == "diff"
        for line in item.excerpt.splitlines()
        for real in changed
    ):
        raise EvidenceMismatch("no diff evidence resembles the commit")
    return finding.model_copy(update={"evidence": evidence}), corrected


async def investigate(lab: Lab, ctx: IncidentContext, question: str | None = None) -> Finding:
    bisected: list[dict] = []
    count = len(await commits_between(lab, ctx.healthy_rev, ctx.broken_rev))
    tools = []
    for tool in build_tools(lab, ctx):
        if tool.name == "git_bisect":
            tool = observed(tool, bisected.append)
            tool = announced(
                tool, ctx.incident_id, NAME, f"Bisecting {count} commits against the failing checkout…"
            )
        tools.append(tool)

    async def bisect_and_report(**args):
        result = await bisect.fn(**args)
        subject = (await lab.git("log", "-1", "--format=%s", result["first_bad_rev"])).strip()
        await note(
            ctx.incident_id, NAME, f"Checkout first breaks at {result['first_bad_rev'][:7]}: {subject}"
        )
        return result

    bisect = next(tool for tool in tools if tool.name == "git_bisect")
    tools[tools.index(bisect)] = replace(bisect, fn=bisect_and_report)

    reasked = corrected = False

    async def check(finding: Finding) -> Finding:
        nonlocal reasked, corrected
        await check_finding(finding, lab, bisected)
        try:
            finding, corrected = await check_evidence(finding, lab, final=reasked)
        except EvidenceMismatch:
            reasked = True
            raise
        return finding

    finding = await run_agent(
        name=NAME,
        system=SYSTEM,
        user=with_question(
            f"Healthy revision: {ctx.healthy_rev}\nBroken revision: {ctx.broken_rev}\n"
            f"Failing request: {ctx.failing_request.model_dump_json()}",
            question,
        ),
        tools=tools,
        model=fast_model(),
        output_model=Finding,
        incident_id=ctx.incident_id,
        check=check,
    )
    if corrected:
        await note(ctx.incident_id, NAME, CORRECTED)
    sha = await lab.resolve(finding.suspect_ref)
    finding = finding.model_copy(update={"agent": NAME, "suspect_ref": sha})
    await add_finding(ctx.incident_id, finding)
    return finding
