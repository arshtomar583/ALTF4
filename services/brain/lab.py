"""Run ShopLite revisions in isolation on port 3003, away from the live slots."""

import asyncio
import atexit
import fcntl
import hashlib
import os
import re
import shutil
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

from .agents.models import FailingRequest
from .checkout import check_charge

PORT = 3003
URL = f"http://127.0.0.1:{PORT}"
HEALTH_TIMEOUT = 10
TEST_TIMEOUT = 15
# The shop's folder inside PHOENIX commits. A repo with the shop at its root works too.
SHOP_DIR = "services/shoplite"
REGRESSION_FILE = "phoenix-regression.test.js"
IDENTITY = ["-c", "user.name=PHOENIX Repair", "-c", "user.email=repair@phoenix.local"]


class LabError(Exception):
    """The lab could not prepare or run a revision."""


@dataclass(frozen=True)
class LabHandle:
    rev: str
    url: str
    pid: int


def log_dir() -> Path:
    return Path(os.getenv("PHOENIX_LOG_DIR") or ".data/logs")


def _node_env(**extra: str) -> dict[str, str]:
    # Candidate code is untrusted, so it never inherits API keys.
    keep = ("PATH", "HOME", "TMPDIR", "SYSTEMROOT")
    return {key: os.environ[key] for key in keep if key in os.environ} | extra


def _kill_group(pid: int) -> None:
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


async def _run(*args: str, cwd: Path, timeout: float = 30, env: dict | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError as error:
        raise LabError(f"{args[0]} timed out after {timeout}s") from error
    finally:
        if process.returncode is None:
            _kill_group(process.pid)
    return process.returncode, output.decode(errors="replace")


class Lab:
    def __init__(self, repo: Path, healthy_rev: str, root: Path | None = None):
        self.repo = repo.resolve()
        self.healthy_rev = healthy_rev
        self.root = (root or Path(os.getenv("PHOENIX_LAB_DIR") or ".data/lab")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir().resolve()
        self.lock = asyncio.Lock()
        self._shop: str | None = None
        self._process: asyncio.subprocess.Process | None = None
        atexit.register(self.stop)

    async def git(self, *args: str, cwd: Path | None = None) -> str:
        code, output = await _run("git", *args, cwd=cwd or self.repo)
        if code:
            raise LabError(f"git {args[0]}: {output.strip()[:500]}")
        return output

    @asynccontextmanager
    async def git_write(self) -> AsyncIterator[None]:
        """Keep two brains from writing to the shared repository at once."""
        with (self.root / "git.lock").open("w") as handle:
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if asyncio.get_running_loop().time() > deadline:
                        raise LabError("Another process is holding the git lock") from None
                    await asyncio.sleep(0.05)
            # Closing the file releases the lock, also when the process is killed.
            yield

    async def resolve(self, rev: str) -> str:
        output = await self.git("rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}")
        return output.strip()

    async def shop(self) -> str:
        """The shop's folder in this repository, or "" when it is the root."""
        if self._shop is None:
            healthy = await self.resolve(self.healthy_rev)
            found = await self.git("ls-tree", "--name-only", healthy, f"{SHOP_DIR}/package.json")
            self._shop = SHOP_DIR if found.strip() else ""
        return self._shop

    async def shop_path(self, name: str) -> str:
        shop = await self.shop()
        return f"{shop}/{name}" if shop else name

    async def shop_git(self, *args: str) -> str:
        """Run git on the shop's files only. The rest of the repository is not the agents' business."""
        return await self.git(*args, "--", await self.shop() or ".")

    async def show(self, rev: str, *options: str) -> str:
        # --first-parent gives a merge commit a real patch instead of an empty one.
        return await self.shop_git("show", "-m", "--first-parent", *options, await self.resolve(rev))

    async def prepare(self) -> None:
        """Install dependencies ahead of time so no agent tool call waits on npm."""
        async with self.lock:
            await self._deps()

    async def _deps(self) -> Path:
        healthy = await self.resolve(self.healthy_rev)
        lockfile = await self.git("show", f"{healthy}:{await self.shop_path('package-lock.json')}")
        manifest = await self.git("show", f"{healthy}:{await self.shop_path('package.json')}")
        digest = hashlib.sha256((manifest + lockfile).encode()).hexdigest()
        deps = self.root / "_deps"
        marker = deps / ".installed"
        if not marker.exists() or marker.read_text() != digest:
            deps.mkdir(exist_ok=True)
            marker.unlink(missing_ok=True)
            (deps / "package.json").write_text(manifest)
            (deps / "package-lock.json").write_text(lockfile)
            npm = ["npm", "ci", "--no-audit", "--no-fund"]
            code, output = await _run(*npm, cwd=deps, timeout=120, env=_node_env())
            if code:
                raise LabError(f"npm ci failed: {output[-500:]}")
            marker.write_text(digest)
        return deps / "node_modules"

    async def _link_deps(self, path: Path) -> None:
        link = path / "node_modules"
        if not link.is_symlink():
            link.symlink_to(await self._deps(), target_is_directory=True)

    async def worktree(self, rev: str) -> Path:
        rev = await self.resolve(rev)
        path = self.root / rev[:12]
        async with self.git_write():
            if (path / ".git").exists():
                await self.git("reset", "-q", "--hard", rev, cwd=path)
                await self.git("clean", "-fdq", "-e", "node_modules", cwd=path)
            else:
                shutil.rmtree(path, ignore_errors=True)
                await self.git("worktree", "prune")
                await self.git("worktree", "add", "-q", "--detach", str(path), rev)
        shop = path / await self.shop()
        await self._link_deps(shop)
        return shop

    def log_path(self, rev: str) -> Path:
        return self.log_dir / f"lab-{rev[:12]}.log"

    def stop(self) -> None:
        if self._process and self._process.returncode is None:
            _kill_group(self._process.pid)
        self._process = None
        self._kill_orphan()

    async def _stop_and_reap(self) -> None:
        process = self._process
        self.stop()
        if process:
            await process.wait()

    def _kill_orphan(self) -> None:
        """Stop a lab server left behind by a brain that was killed."""
        pidfile = self.root / "lab.pid"
        with suppress(OSError, ValueError):
            pid = int(pidfile.read_text())
            if b"src/server.js" in Path(f"/proc/{pid}/cmdline").read_bytes():
                _kill_group(pid)
        pidfile.unlink(missing_ok=True)

    async def run_rev(self, rev: str) -> LabHandle:
        async with self.lock:
            return await self._start(rev)

    async def _start(self, rev: str) -> LabHandle:
        rev = await self.resolve(rev)
        path = await self.worktree(rev)
        await self._stop_and_reap()
        log = self.log_path(rev)
        log.parent.mkdir(parents=True, exist_ok=True)
        # Append: bisect may restart a revision while its earlier logs are being read.
        with log.open("ab") as output:
            log_start = output.tell()
            process = await asyncio.create_subprocess_exec(
                "node",
                "src/server.js",
                cwd=path,
                env=_node_env(PORT=str(PORT), SHOP_REVISION=rev, NODE_ENV="production"),
                stdout=output,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        self._process = process
        (self.root / "lab.pid").write_text(str(process.pid))

        deadline = asyncio.get_running_loop().time() + HEALTH_TIMEOUT
        async with httpx.AsyncClient(timeout=1) as client:
            while asyncio.get_running_loop().time() < deadline:
                if process.returncode is not None:
                    raise LabError(f"{rev[:12]} exited on start: {self.logs(rev, log_start)[:500]}")
                with suppress(httpx.RequestError):
                    health = await client.get(f"{URL}/health")
                    # The revision check catches another process holding the port.
                    if health.is_success and health.headers.get("x-shop-revision") == rev:
                        return LabHandle(rev, URL, process.pid)
                await asyncio.sleep(0.05)
        self.stop()
        raise LabError(f"{rev[:12]} was not healthy within {HEALTH_TIMEOUT}s")

    async def replay(self, rev: str, request: FailingRequest) -> dict:
        """Send one request to a fresh server. status 0 means it never came up."""
        async with self.lock:
            try:
                handle = await self._start(rev)
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.request(
                        request.method, handle.url + request.path, json=request.body
                    )
            except (LabError, httpx.RequestError) as error:
                return {"status": 0, "body_excerpt": f"{type(error).__name__}: {error}", "headers": {}}
            finally:
                await self._stop_and_reap()
        return {
            "status": response.status_code,
            "checkout_ok": check_charge(response, request.body)[0] if isinstance(request.body, dict) and request.path == "/checkout" else None,
            "body_excerpt": response.text[:500],
            "headers": dict(response.headers),
        }

    async def run_tests(self, rev: str, extra_test: str | None = None) -> dict:
        """Run the revision's own tests, or only extra_test when its source is given."""
        async with self.lock:
            path = await self.worktree(rev)
            if extra_test is None:
                return await self.test_path(path)
            target = path / "test" / REGRESSION_FILE
            target.parent.mkdir(exist_ok=True)
            target.write_text(extra_test)
            try:
                return await self.test_path(path, f"test/{REGRESSION_FILE}")
            finally:
                target.unlink(missing_ok=True)

    async def test_path(self, path: Path, *files: str) -> dict:
        command = ["node", "--test", "--test-reporter=tap", *files]
        try:
            code, output = await _run(*command, cwd=path, timeout=TEST_TIMEOUT, env=_node_env())
        except LabError as error:
            return {"ok": False, "passed": 0, "failed": 1, "output_excerpt": str(error)}
        counts = {key: int(value) for key, value in re.findall(r"^# (pass|fail) (\d+)", output, re.M)}
        passed, failed = counts.get("pass", 0), counts.get("fail", 0)
        return {
            "ok": code == 0 and passed > 0 and failed == 0,
            "passed": passed,
            "failed": failed,
            "output_excerpt": output[:2_500],
        }

    async def candidate_branch(self, base_rev: str, incident_id: str, n: int | str) -> tuple[Path, str]:
        slug = re.sub(r"[^A-Za-z0-9_-]", "-", re.sub(r"^inc[-_]", "", incident_id))
        branch = f"phoenix/inc-{slug}-c{n}"
        path = self.root / f"candidate-{slug}-c{n}"
        base = await self.resolve(base_rev)
        async with self.lock, self.git_write():
            # A brain killed mid-repair leaves a half-written worktree: start it over.
            shutil.rmtree(path, ignore_errors=True)
            await self.git("worktree", "prune")
            await self.git("worktree", "add", "-q", "-B", branch, str(path), base)
            shop = path / await self.shop()
            await self._link_deps(shop)
        return shop, branch

    async def commit(self, path: Path, message: str) -> str:
        if not path.resolve().is_relative_to(self.root):
            raise LabError("Commits are only allowed in lab worktrees")
        # Ignore the dependency symlink even when the shop has no .gitignore.
        exclude = self.root / "exclude"
        exclude.write_text("node_modules\n")
        async with self.git_write():
            await self.git("-c", f"core.excludesFile={exclude}", "add", "-A", cwd=path)
            await self.git(*IDENTITY, "commit", "-q", "-m", message, cwd=path)
        sha = (await self.git("rev-parse", "HEAD", cwd=path)).strip()
        # The version switch deploys from the shared repository, so the commit must be there.
        if await self.resolve(sha) != sha:
            raise LabError(f"{sha[:12]} is not in the shared repository")
        return sha

    def logs(self, rev: str, start: int = 0) -> str:
        path = self.log_path(rev)
        return path.read_bytes()[start:].decode(errors="replace") if path.exists() else ""


@lru_cache(maxsize=1)
def get_lab(repo: Path, healthy_rev: str) -> Lab:
    """The shared lab: one instance, so its lock guards the single lab port."""
    return Lab(repo, healthy_rev)
