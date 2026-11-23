"""The Verifier's tools. They see three revisions and nothing about how the fix was made."""

import json
import re

from ..agents.models import FailingRequest
from ..lab import Lab
from ..llm import Tool

ALLOWED_IMPORT = re.compile(r"node:assert(/strict)?|node:test|supertest|\.\./src/[\w./-]+\.js")
FORBIDDEN = {
    r"\bimport\s*\(": "dynamic import",
    r"\brequire\b": "require",
    r"\bprocess\b": "process",
    r"\bglobalThis\b": "globalThis",
    r"\bfetch\s*\(": "fetch",
    r"\b(skip|todo|only)\b": "skip, todo or only",
    r"\beval\b|\bFunction\s*\(": "eval",
}


def check_test(code: str) -> None:
    """A regression test may only drive the shop. It cannot inspect files or the environment."""
    sources = re.findall(r"""\bimport\b[^;'"]*['"]([^'"]+)['"]""", code)
    if bad := [source for source in sources if not ALLOWED_IMPORT.fullmatch(source)]:
        raise ValueError(f"These imports are not allowed in a regression test: {bad}")
    for pattern, name in FORBIDDEN.items():
        if re.search(pattern, code):
            raise ValueError(f"A regression test may not use {name}")
    if not re.search(r"\btest\s*\(", code) or "assert" not in code:
        raise ValueError("Write at least one test() with an assertion")


class Bench:
    """One verification: its regression test and the results measured in the lab."""

    def __init__(self, lab: Lab, revs: dict[str, str], diff: str, failing: FailingRequest):
        self.lab, self.revs, self.diff, self.failing = lab, revs, diff, failing
        self.test: str | None = None
        self._results: dict[str, dict] = {}
        self._unit: dict[str, dict] = {}

    def _rev(self, rev: str) -> str:
        for name, sha in self.revs.items():
            if rev == name or (len(rev) >= 7 and sha.startswith(rev)):
                return sha
        raise ValueError(f"rev must be one of {', '.join(self.revs)}")

    async def lab_replay(self, rev: str, body_json: str | None = None) -> dict:
        request = self.failing
        if body_json:
            request = request.model_copy(update={"body": json.loads(body_json)})
        return await self.lab.replay(self._rev(rev), request)

    async def write_regression_test(self, code: str) -> dict:
        check_test(code)
        self.test = code
        self._results.clear()
        return {"saved": True}

    async def run_regression(self, rev: str) -> dict:
        if self.test is None:
            raise ValueError("Call write_regression_test first")
        sha = self._rev(rev)
        if sha not in self._results:
            self._results[sha] = await self.lab.run_tests(sha, self.test)
        return self._results[sha]

    async def run_unit_tests(self, rev: str) -> dict:
        sha = self._rev(rev)
        if sha not in self._unit:
            self._unit[sha] = await self.lab.run_tests(sha)
        return self._unit[sha]

    async def read_candidate_diff(self) -> str:
        return self.diff

    def tools(self) -> list[Tool]:
        rev = {"type": "string", "enum": list(self.revs)}

        def schema(**properties: dict) -> dict:
            return {"type": "object", "properties": properties, "required": list(properties)}

        return [
            Tool(
                "lab_replay",
                "Send the failing request to one revision in the lab. body_json replaces its body.",
                # A string, because models fill an object without listed properties with {}.
                {
                    "type": "object",
                    "properties": {"rev": rev, "body_json": {"type": "string"}},
                    "required": ["rev"],
                },
                self.lab_replay,
            ),
            Tool(
                "write_regression_test",
                "Save your node:test file. It is placed in test/ and replaces any earlier one.",
                schema(code={"type": "string"}),
                self.write_regression_test,
            ),
            Tool(
                "run_regression", "Run your regression test on one revision.", schema(rev=rev),
                self.run_regression,
            ),
            Tool(
                "run_unit_tests", "Run a revision's own tests.", schema(rev=rev), self.run_unit_tests
            ),
            Tool("read_candidate_diff", "Read the candidate's diff.", schema(), self.read_candidate_diff),
        ]
