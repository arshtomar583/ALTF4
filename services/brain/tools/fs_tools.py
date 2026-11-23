"""The Repair agent's tools, confined to one candidate worktree."""

from pathlib import Path, PurePosixPath

from ..lab import SHOP_DIR, Lab
from ..llm import Tool

MAX_READ = 3_500


class Workspace:
    """One candidate worktree. Commits need passing unit tests on the final content."""

    def __init__(self, lab: Lab, path: Path, base: str):
        self.lab, self.path, self.base = lab, path.resolve(), base
        self.tested = False
        self.rev: str | None = None

    def _inside(self, path: str) -> Path:
        # Diffs name files from the repository root, so accept those paths too.
        path = path.removeprefix(f"{SHOP_DIR}/")
        parts = PurePosixPath(path.replace("\\", "/")).parts
        target = (self.path / path).resolve()
        if not parts or ".." in parts or not target.is_relative_to(self.path):
            raise PermissionError(f"{path} is outside the candidate worktree")
        if ".git" in parts or any(part.startswith(".env") for part in parts):
            raise PermissionError(f"{path} is off limits")
        return target

    async def read_file(self, path: str, start_line: int = 1) -> str:
        lines = self._inside(path).read_text().splitlines(keepends=True)
        return "".join(lines[max(0, start_line - 1) :])[:MAX_READ]

    async def write_file(self, path: str, content: str) -> dict:
        target = self._inside(path)
        relative = target.relative_to(self.path)
        names = [part.lower() for part in relative.parts]
        if any(name in ("test", "tests", "__tests__") or ".test." in name for name in names):
            raise PermissionError("Tests cannot be changed")
        if names[0] != "src":
            raise PermissionError("Only files in src/ can be changed")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.tested = False
        return {"written": str(relative)}

    async def run_unit_tests(self) -> dict:
        async with self.lab.lock:
            result = await self.lab.test_path(self.path)
        self.tested = result["ok"]
        return result

    async def commit_candidate(self, message: str) -> dict:
        if not self.tested:
            raise PermissionError("Unit tests must pass on the current files before you commit")
        self.rev = await self.lab.commit(self.path, message)
        return {"rev": self.rev}

    async def diff(self) -> str:
        return await self.lab.git("diff", self.base, self.rev or self.base)

    def tools(self) -> list[Tool]:
        text = {"type": "string"}
        return [
            Tool(
                "read_file",
                "Read a file, from start_line when it is long.",
                {
                    "type": "object",
                    "properties": {"path": text, "start_line": {"type": "integer"}},
                    "required": ["path"],
                },
                self.read_file,
            ),
            Tool(
                "write_file",
                "Replace a whole file in src/ with new content.",
                {
                    "type": "object",
                    "properties": {"path": text, "content": text},
                    "required": ["path", "content"],
                },
                self.write_file,
            ),
            Tool(
                "run_unit_tests",
                "Run the shop's own tests on your files.",
                {"type": "object", "properties": {}},
                self.run_unit_tests,
            ),
            Tool(
                "commit_candidate",
                "Commit your change. Needs passing unit tests.",
                {"type": "object", "properties": {"message": text}, "required": ["message"]},
                self.commit_candidate,
            ),
        ]
