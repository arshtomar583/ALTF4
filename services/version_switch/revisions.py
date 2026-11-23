"""Deploy committed ShopLite source through a private Git worktree per slot."""

import re
import subprocess
from pathlib import Path
from typing import Literal

Slot = Literal["a", "b"]
HEALTHY_REF = "refs/phoenix/healthy"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")


class RevisionError(RuntimeError):
    pass


class SlotRevisions:
    def __init__(self, root: Path, source: Path):
        self.root = root
        self.source = source
        self.repo = root / "repo"

    def _git(self, path: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise RevisionError(f"Git {args[0]} failed for the PHOENIX revision store") from error
        return result.stdout.strip()

    def _worktree(self, slot: Slot) -> Path:
        return self.root / f"slot-{slot}"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.repo / ".git").is_dir():
            self._git(self.root, "clone", "--no-local", "--no-checkout", str(self.source), str(self.repo))
        try:
            healthy = self.healthy_revision()
        except RevisionError:
            healthy = self._git(self.repo, "rev-parse", "--verify", "origin/main^{commit}")
            self._git(self.repo, "update-ref", HEALTHY_REF, healthy)
        for slot in ("a", "b"):
            worktree = self._worktree(slot)
            if not (worktree / ".git").is_file():
                self._git(self.repo, "worktree", "add", "--detach", str(worktree), healthy)
            self._publish(slot, self._git(worktree, "rev-parse", "HEAD"))

    def healthy_revision(self) -> str:
        return self._git(self.repo, "rev-parse", "--verify", f"{HEALTHY_REF}^{{commit}}")

    def promote(self, slot: Slot, revision: str) -> None:
        if self.revision(slot) != revision:
            raise RevisionError("The active slot no longer runs the requested revision")
        self._git(self.repo, "update-ref", HEALTHY_REF, revision)

    def revision(self, slot: Slot) -> str:
        return (self.root / f"slot-{slot}.revision").read_text(encoding="ascii").strip()

    def _publish(self, slot: Slot, revision: str) -> None:
        marker = self.root / f"slot-{slot}.revision"
        pending = marker.with_suffix(".pending")
        pending.write_text(revision + "\n", encoding="ascii")
        pending.replace(marker)

    def checkout_rev(self, slot: Slot, revision: str) -> None:
        if not SHA_PATTERN.fullmatch(revision):
            raise RevisionError("A full Git commit SHA is required")
        try:
            commit = self._git(self.repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
        except RevisionError:
            self._git(self.repo, "fetch", "--no-tags", "origin", revision)
            commit = self._git(self.repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
        if commit != revision:
            raise RevisionError("The requested SHA must identify a commit directly")
        worktree = self._worktree(slot)
        self._git(worktree, "checkout", "--detach", revision)
        actual = self._git(worktree, "rev-parse", "HEAD")
        if actual != revision:
            raise RevisionError(f"Slot {slot} did not check out the requested commit")
        self._publish(slot, actual)
