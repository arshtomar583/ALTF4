"""Write the incident report from recorded events only. Nothing is estimated."""

import base64
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .agents import bridge
from .agents.base import note

GITHUB = "https://api.github.com"
NOT_RECORDED = "not recorded"
MARK = {"pass": "✓", "fail": "✗"}


def _incident(incident_id: str) -> tuple[list[dict], list[dict]]:
    every = bridge.events()
    own = [event for event in every if event["incident_id"] == incident_id]
    if not own:
        raise LookupError(f"No events are recorded for {incident_id}")
    return every, own


def _first(events: list[dict], type_: str) -> dict | None:
    return next((event for event in events if event["type"] == type_), None)


def _elapsed(start: dict | None, end: dict | None) -> str:
    if not start or not end:
        return NOT_RECORDED
    delta = datetime.fromisoformat(end["occurred_at"]) - datetime.fromisoformat(start["occurred_at"])
    return f"{delta.total_seconds():.1f} s"


def _cell(text: Any) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def _block(text: str, language: str = "") -> list[str]:
    # A fence longer than any inside the text keeps it contained.
    longest = max((len(run) for run in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * (longest + 1)
    return ["", f"{fence}{language}", text.rstrip(), fence]


def _failed_checkouts(every: list[dict], own: list[dict]) -> tuple[str, str]:
    opened, restored = _first(own, "incident.opened"), _first(own, "incident.restored")
    if not opened or not restored:
        return NOT_RECORDED, NOT_RECORDED
    # The break precedes the incident it causes, but follows any earlier incident.
    earlier = [
        event["id"]
        for event in every
        if event["type"] == "incident.opened" and event["id"] < opened["id"]
    ]
    breaks = [
        event["id"]
        for event in every
        if event["type"] == "chaos.break" and max(earlier, default=0) < event["id"] < opened["id"]
    ]
    start = breaks[-1] if breaks else opened["id"]
    failed = [
        event["data"]
        for event in every
        if event["type"] == "checkout.probe"
        and start < event["id"] < restored["id"]
        and not event["data"].get("ok")
    ]
    totals = [probe["cart_total_cents"] for probe in failed if probe.get("cart_total_cents") is not None]
    if not failed:
        value = "0 cents"
    elif not totals:
        value = NOT_RECORDED
    else:
        value = f"{sum(totals)} cents across {len(totals)} of {len(failed)} carts with a recorded total"
    return str(len(failed)), value


def _finding(title: str, event: dict | None) -> list[str]:
    if event is None:
        return ["", f"### {title}", "", "No finding."]
    data = event["data"]
    lines = [
        "",
        f"### {title}",
        "",
        _cell(data["claim"]),
        "",
        f"- Confidence: {data['confidence']:.0%}",
        f"- Suspect: {_cell(data['suspect_type'])} {_cell(data['suspect_ref'] or '')}".rstrip(),
    ]
    for item in data["evidence"]:
        where = " ".join(part for part in (item.get("ref"), item.get("url")) if part)
        lines += ["", f"{_cell(item['kind'])}: {_cell(where)}".rstrip(": ")]
        lines += _block(item["excerpt"], "diff" if item["kind"] == "diff" else "")
    return lines


def _last(own: list[dict], type_: str, actor: str | None = None) -> dict | None:
    return next(
        (
            event
            for event in reversed(own)
            if event["type"] == type_ and actor in (None, event["actor"])
        ),
        None,
    )


def _verified(own: list[dict]) -> dict | None:
    return next(
        (
            event
            for event in own
            if event["type"] == "verification.done" and event["data"]["verdict"] == "VERIFIED"
        ),
        None,
    )


def render(incident_id: str) -> str:
    every, own = _incident(incident_id)
    opened = _first(own, "incident.opened")
    failed, value = _failed_checkouts(every, own)
    lines = [
        f"# Incident report: {incident_id}",
        "",
        "Every figure below is computed from recorded events.",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Time to restore | {_elapsed(opened, _first(own, 'incident.restored'))} |",
        f"| Time to verified fix | {_elapsed(opened, _verified(own))} |",
        f"| Failed checkouts | {failed} |",
        f"| Value of failed carts | {value} |",
        "",
        "## Findings",
        *_finding("A: Code Investigator", _last(own, "finding.added", "code_investigator")),
        *_finding("B: Runtime Investigator", _last(own, "finding.added", "runtime_investigator")),
        "",
        "## Decision",
        "",
    ]
    if decision := _last(own, "decision.made"):
        data = decision["data"]
        lines += [f"**{_cell(data['remedy'])}**: {_cell(data['reasoning'])}"]
    if escalated := _last(own, "incident.escalated"):
        lines += ["", f"**Escalated to a human:** {_cell(escalated['data']['reason'])}"]
    elif not decision:
        lines += ["No decision recorded."]

    lines += ["", "## Candidates", ""]
    proposals = [event for event in own if event["type"] == "candidate.proposed"]
    results = {e["data"]["candidate_id"]: e["data"] for e in own if e["type"] == "verification.done"}
    if not proposals:
        lines += ["No candidate was proposed."]
    else:
        lines += [
            "| Candidate | By | Broken | Healthy | Candidate | Unit tests | Diff review | Verdict | Why |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    for event in proposals:
        data = event["data"]
        result = results.get(data["candidate_id"])
        if result is None:
            row = ["–"] * 5 + ["not verified", ""]
        else:
            row = [MARK[result[key]] for key in ("broken", "healthy", "candidate", "unit_tests")]
            row += ["✓" if result["diff_review_ok"] else "✗", result["verdict"], result["reason"]]
        cells = [data["candidate_id"], data["by"], *row]
        lines += ["| " + " | ".join(_cell(cell) for cell in cells) + " |"]
    for event in proposals:
        lines += ["", f"### {_cell(event['data']['candidate_id'])} at {event['data']['rev'][:12]}"]
        lines += _block(event["data"]["diff"], "diff")

    lines += ["", "## Release", ""]
    if release := _last(own, "release.done"):
        data = release["data"]
        outcome = "Released" if data.get("ok") else "Stopped"
        detail = data.get("reason") or f"slot {str(data.get('active_slot', '')).upper()}"
        lines += [f"{outcome}: {_cell(data.get('candidate_id', ''))} ({_cell(detail)})"]
    else:
        lines += ["No release recorded."]

    lines += ["", "## Timeline (UTC)", "", "| Time | Who | What |", "| --- | --- | --- |"]
    for event in own:
        if event["type"] in ("agent.tool", "report.ready"):
            continue
        time = datetime.fromisoformat(event["occurred_at"]).astimezone(timezone.utc)
        what = event["summary"] or event["type"]
        lines += [f"| {time:%H:%M:%S} | {_cell(event['actor'])} | {_cell(what)} |"]
    return "\n".join(lines) + "\n"


async def build_report(incident_id: str) -> str:
    """Render the report, open a PR when GitHub is configured, and announce it."""
    markdown = render(incident_id)
    pr_url = None
    try:
        pr_url = await _publish(incident_id, markdown)
    except _Skip as reason:
        await note(incident_id, "report", f"PR skipped: {reason}")
    except httpx.HTTPStatusError as error:
        await note(incident_id, "report", f"PR failed: GitHub answered {error.response.status_code}")
    except (httpx.RequestError, KeyError, ValueError) as error:
        await note(incident_id, "report", f"PR failed: {type(error).__name__}")
    await bridge.emit(
        "report.ready",
        incident_id=incident_id,
        actor="report",
        summary="The incident report is ready" + (" with a pull request" if pr_url else ""),
        data={"markdown": markdown, "pr_url": pr_url},
    )
    return markdown


class _Skip(Exception):
    pass


async def _publish(incident_id: str, markdown: str) -> str:
    token, repo = os.getenv("GITHUB_TOKEN"), os.getenv("GITHUB_REPO")
    if not token:
        raise _Skip("no GitHub token")
    if not repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise _Skip("no GitHub repository is set")
    if repo.split("/")[1].lower().removesuffix(".git") == "altf4":
        raise _Skip("the submission repository is off limits")
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", re.sub(r"^inc[-_]", "", incident_id))
    branch = f"phoenix/inc-{slug}"
    files = {f"incidents/inc-{slug}.md": markdown}
    _, own = _incident(incident_id)
    if verified := _verified(own):
        wanted = verified["data"]["candidate_id"]
        for event in own:
            if event["type"] == "candidate.proposed" and event["data"]["candidate_id"] == wanted:
                files[f"incidents/inc-{slug}.patch"] = event["data"]["diff"]

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    async with httpx.AsyncClient(base_url=f"{GITHUB}/repos/{repo}", headers=headers, timeout=15) as client:

        async def call(method: str, path: str, *allowed: int, **kwargs: Any) -> httpx.Response:
            response = await client.request(method, path, **kwargs)
            if response.status_code not in allowed:
                response.raise_for_status()
            return response

        base = (await call("GET", "")).json()["default_branch"]
        # A second report for the same incident updates the branch and pull request it already has.
        if (await call("GET", f"/git/ref/heads/{branch}", 404)).status_code == 404:
            sha = (await call("GET", f"/git/ref/heads/{base}")).json()["object"]["sha"]
            await call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
        for path, text in files.items():
            current = await call("GET", f"/contents/{path}", 404, params={"ref": branch})
            body = {
                "message": f"Add {path}",
                "content": base64.b64encode(text.encode()).decode(),
                "branch": branch,
            }
            if current.status_code != 404:
                body["sha"] = current.json()["sha"]
            await call("PUT", f"/contents/{path}", json=body)
        # GitHub rejects bodies over 65,536 characters; the full report is in the branch.
        pull = {"title": f"Incident {incident_id}", "body": markdown[:60_000]}
        head = {"head": f"{repo.split('/')[0]}:{branch}", "state": "open"}
        if existing := (await call("GET", "/pulls", params=head)).json():
            await call("PATCH", f"/pulls/{existing[0]['number']}", json=pull)
            return existing[0]["html_url"]
        created = await call("POST", "/pulls", json=pull | {"head": branch, "base": base})
        return created.json()["html_url"]
