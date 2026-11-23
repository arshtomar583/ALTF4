"""Log readers for the Runtime Investigator."""

import asyncio
from pathlib import Path

from .. import lab as lab_module
from ..lab import Lab, LabError
from ..llm import Tool

TAIL = 3_500


async def container_logs(slot: str, since_s: int = 120) -> str | dict:
    if slot not in ("a", "b"):
        return {"error": "slot must be a or b"}
    reason = "docker is not available"
    try:
        code, output = await lab_module._run(
            "docker", "logs", "--since", f"{int(since_s)}s", f"phoenix-shop-{slot}-1",
            cwd=Path.cwd(), timeout=10,
        )
        if code == 0 and output.strip():
            return output[-TAIL:]
        reason = output.strip()[:200] or "docker returned no logs"
    except (OSError, LabError) as error:
        reason = str(error)
    path = lab_module.log_dir() / f"{slot}.log"
    if path.exists():
        text = await asyncio.to_thread(path.read_text, errors="replace")
        if text.strip():
            return text[-TAIL:]
    return {"error": f"No logs for slot {slot}: {reason}"}


def log_tools(lab: Lab, broken_slot: str, broken_rev: str) -> list[Tool]:
    async def slot_logs(slot: str, since_s: int = 120) -> str | dict:
        output = await container_logs(slot, since_s)
        if isinstance(output, str) or slot != broken_slot:
            return output
        # Inside Docker the brain cannot read other containers. The lab replayed this same request.
        text = lab.logs(await lab.resolve(broken_rev))
        if not text.strip():
            return output
        return {"source": f"lab replay of the failing request on {broken_rev[:12]}", "logs": text[-TAIL:]}

    async def lab_logs(rev: str) -> str | dict:
        text = lab.logs(await lab.resolve(rev))
        return text[-TAIL:] if text.strip() else {"error": f"The lab has no logs for {rev}"}

    return [
        Tool(
            "container_logs",
            "Read recent stdout and stderr of a live shop slot.",
            {
                "type": "object",
                "properties": {
                    "slot": {"type": "string", "enum": ["a", "b"]},
                    "since_s": {"type": "integer", "default": 120},
                },
                "required": ["slot"],
            },
            slot_logs,
        ),
        Tool(
            "lab_logs",
            "Read what a revision printed when the failing request was replayed in the lab.",
            {"type": "object", "properties": {"rev": {"type": "string"}}, "required": ["rev"]},
            lab_logs,
        ),
    ]
