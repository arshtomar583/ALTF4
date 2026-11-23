"""Read-only history tools for the Code Investigator."""

import json
from pathlib import PurePosixPath

from ..agents.models import FailingRequest
from ..lab import Lab
from ..llm import Tool

REV = {"type": "string", "description": "A commit SHA or tag"}
# A shop that never starts looks like a gateway error through the version switch.
GATEWAY_ERRORS = (502, 503, 504)


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _reproduces(result: dict, request: FailingRequest) -> bool:
    if result["status"] == 0:
        return request.status in GATEWAY_ERRORS
    return result["status"] == request.status


async def git_diff(lab: Lab, a: str, b: str) -> str:
    return await lab.shop_git("diff", await lab.resolve(a), await lab.resolve(b))


async def git_show(lab: Lab, rev: str) -> str:
    return await lab.show(rev, "--stat", "--patch")


async def commits_between(lab: Lab, good: str, bad: str) -> list[str]:
    """Commits that touched the shop, oldest first."""
    return (await lab.shop_git("rev-list", "--reverse", "--first-parent", f"{good}..{bad}")).split()


async def git_bisect(lab: Lab, good: str, bad: str, request: FailingRequest) -> dict:
    """Find the first commit in good..bad that reproduces the failing request."""
    good, bad = await lab.resolve(good), await lab.resolve(bad)
    commits = await commits_between(lab, good, bad)
    if not commits:
        raise ValueError("There are no commits between good and bad")
    checks: list[dict] = []

    async def is_bad(rev: str) -> bool:
        result = await lab.replay(rev, request)
        verdict = _reproduces(result, request)
        checks.append({"rev": rev, "status": result["status"], "bad": verdict})
        return verdict

    if await is_bad(good):
        raise ValueError("The good revision also fails, so bisect cannot run")
    if not await is_bad(bad):
        raise ValueError("The bad revision does not reproduce the failure")

    low, high = 0, len(commits) - 1
    while low < high:
        middle = (low + high) // 2
        if await is_bad(commits[middle]):
            high = middle
        else:
            low = middle + 1
    first_bad = commits[high]
    return {"first_bad_rev": first_bad, "checks": checks, "diff": await git_show(lab, first_bad)}


async def _file(lab: Lab, rev: str, path: str) -> str:
    return await lab.git("show", f"{rev}:{path}")


async def _config_files(lab: Lab, rev: str) -> set[str]:
    paths = (await lab.shop_git("ls-tree", "-r", "-z", "--name-only", rev)).split("\0")
    return {
        path
        for path in paths
        if (name := PurePosixPath(path).name).startswith(".env")
        or name in ("package.json", "package-lock.json")
    }


def _settings(path: str, text: str) -> dict[str, str]:
    """Flatten one config file so two versions can be compared key by key."""
    if PurePosixPath(path).name.startswith(".env"):
        pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line)
        return {
            key.strip().removeprefix("export "): value.strip()
            for key, value in pairs
            if not key.strip().startswith("#")
        }
    try:
        data = json.loads(text)
    except ValueError:
        return {"(invalid JSON)": text[:200]}
    if path.endswith("package-lock.json"):
        packages = data.get("packages", {})
        return {name: str(info.get("version")) for name, info in packages.items() if name}
    flat = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat |= {f"{key}.{name}": json.dumps(item) for name, item in value.items()}
        else:
            flat[key] = json.dumps(value)
    return flat


async def config_diff(lab: Lab, a: str, b: str) -> dict:
    """Summarise config changes. Env values may be secrets, so only their keys appear."""
    a, b = await lab.resolve(a), await lab.resolve(b)
    before_files, after_files = await _config_files(lab, a), await _config_files(lab, b)
    changes = []
    for path in sorted(before_files | after_files):
        before = _settings(path, await _file(lab, a, path)) if path in before_files else {}
        after = _settings(path, await _file(lab, b, path)) if path in after_files else {}
        secret = PurePosixPath(path).name.startswith(".env")
        for key in sorted(before.keys() | after.keys()):
            if before.get(key) == after.get(key):
                continue
            kind = "removed" if key not in after else "added" if key not in before else "changed"
            change = {"file": path, "key": key, "change": kind}
            if not secret:
                change |= {"before": before.get(key), "after": after.get(key)}
            changes.append(change)
    # Compare whole files too, so a change no setting captures still shows up.
    for path in sorted(before_files & after_files - {change["file"] for change in changes}):
        if await _file(lab, a, path) != await _file(lab, b, path):
            changes.append({"file": path, "key": None, "change": "edited"})
    return {
        "config_changed": bool(changes),
        "files_compared": sorted(before_files | after_files),
        "changes": changes,
    }


def config_tool(lab: Lab) -> Tool:
    async def diff(a: str, b: str) -> dict:
        return await config_diff(lab, a, b)

    return Tool(
        "config_diff",
        "Summarise .env, package.json and dependency changes between two revisions.",
        _schema({"a": REV, "b": REV}, ["a", "b"]),
        diff,
    )


def git_tools(lab: Lab, request: FailingRequest, head: str) -> list[Tool]:
    async def log(n: int = 10) -> str:
        return await lab.shop_git("log", f"-{max(1, min(int(n), 50))}", "--format=%H %s", head)

    async def show(rev: str) -> str:
        return await git_show(lab, rev)

    async def diff(a: str, b: str) -> str:
        return await git_diff(lab, a, b)

    async def bisect(good: str, bad: str) -> dict:
        return await git_bisect(lab, good, bad, request)

    return [
        Tool(
            "git_log",
            "List recent commits, newest first.",
            _schema({"n": {"type": "integer", "default": 10}}, []),
            log,
        ),
        Tool("git_show", "Show one commit with its patch.", _schema({"rev": REV}, ["rev"]), show),
        Tool(
            "git_diff",
            "Diff two revisions.",
            _schema({"a": REV, "b": REV}, ["a", "b"]),
            diff,
        ),
        Tool(
            "git_bisect",
            "Replay the failing request across history and return the first bad commit with its diff.",
            _schema({"good": REV, "bad": REV}, ["good", "bad"]),
            bisect,
        ),
    ]
