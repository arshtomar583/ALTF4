import os
import sys
from pathlib import Path

import pytest

from brain.agents.models import FailingRequest
from brain.lab import Lab, LabError
from brain.tools.git_tools import git_bisect

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from dev_incident import build_shop_repo  # noqa: E402

pytestmark = pytest.mark.asyncio
REQUEST = FailingRequest(body={"sku": "demo-mug", "quantity": 1}, status=500)
TEST = """
import assert from "node:assert/strict";
import { test } from "node:test";
import request from "supertest";
import { createShop } from "../src/shop.js";
test("plain cart", async () => {
  const response = await request(createShop("t")).post("/checkout").send({ sku: "demo-mug", quantity: 1 });
  assert.equal(response.status, 200);
  assert.equal(response.body.totalCents, 1200);
});
"""


@pytest.fixture(scope="module")
def folder(tmp_path_factory):
    return tmp_path_factory.mktemp("lab")


@pytest.fixture(scope="module")
def revs(folder):
    # The other test files cover the team layout, with the shop in services/shoplite.
    return build_shop_repo(folder / "shop", "")


@pytest.fixture
def lab(folder, revs):
    lab = Lab(folder / "shop", revs["healthy"], folder / "lab")
    yield lab
    lab.stop()


async def test_same_request_fails_on_broken_and_passes_on_healthy(lab, revs):
    assert (await lab.replay(revs["healthy"], REQUEST))["status"] == 200
    broken = await lab.replay(revs["broken"], REQUEST)
    assert broken["status"] == 500
    assert broken["headers"]["x-shop-revision"] == revs["broken"]
    assert "TypeError" in lab.logs(revs["broken"])


async def test_bisect_finds_the_culprit_not_the_head(lab, revs):
    result = await git_bisect(lab, revs["healthy"], revs["broken"], REQUEST)
    assert result["first_bad_rev"] == revs["culprit"] != revs["broken"]
    assert "-    const code = request.body?.discount?.code;" in result["diff"]


async def test_bisect_rejects_a_bad_revision_that_works(lab, revs):
    with pytest.raises(ValueError, match="does not reproduce"):
        await git_bisect(lab, revs["healthy"], revs["healthy"], REQUEST)


async def test_bisect_distinguishes_two_success_responses_with_different_prices(lab, revs):
    path, _ = await lab.candidate_branch(revs["healthy"], "wrong-price", 1)
    shop = path / "src" / "shop.js"
    shop.write_text(shop.read_text().replace("totalCents: subtotalCents - discountCents", "totalCents: subtotalCents"))
    bad = await lab.commit(path, "Omit discount from checkout total")
    request = FailingRequest(body={**REQUEST.body, "discount": {"code": "SAVE10"}}, status=200)
    result = await git_bisect(lab, revs["healthy"], bad, request)
    assert result["first_bad_rev"] == bad


async def test_bisect_finds_the_culprit_when_healthy_is_a_released_fix(lab, revs):
    path, _ = await lab.candidate_branch(revs["broken"], "released", 1)
    shop = path / "src" / "shop.js"
    broken = "request.body.discount.code.trim().toUpperCase()"
    shop.write_text(shop.read_text().replace(broken, "request.body?.discount?.code"))
    fix = await lab.commit(path, "Fix")
    result = await git_bisect(lab, fix, revs["broken"], REQUEST)
    assert result["first_bad_rev"] == revs["culprit"]


async def test_regression_test_separates_the_revisions(lab, revs):
    assert (await lab.run_tests(revs["healthy"], TEST))["ok"]
    assert not (await lab.run_tests(revs["broken"], TEST))["ok"]
    path = await lab.worktree(revs["healthy"])
    assert await lab.git("status", "--porcelain", cwd=path) == ""


async def test_candidate_commits_stay_out_of_main(lab, revs):
    path, branch = await lab.candidate_branch(revs["broken"], "inc-1", 1)
    (path / "src" / "note.js").write_text("export {};\n")
    sha = await lab.commit(path, "Add note")
    assert (await lab.git("diff", "--name-only", revs["broken"], sha)).split() == ["src/note.js"]
    assert branch == "phoenix/inc-1-c1"
    assert (await lab.resolve("main")) == revs["broken"]

    path, _ = await lab.candidate_branch(revs["broken"], "inc-1", 1)
    assert not (path / "src" / "note.js").exists()
    with pytest.raises(LabError, match="lab worktrees"):
        await lab.commit(lab.repo, "no")


async def test_secrets_never_reach_shop_code(lab, revs, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret")
    result = await lab.run_tests(
        revs["healthy"],
        'import { test } from "node:test"; import assert from "node:assert";'
        'test("env", () => assert.equal(process.env.ANTHROPIC_API_KEY, undefined));',
    )
    assert result["ok"], result


async def test_server_left_by_a_killed_brain_is_replaced(lab, folder, revs):
    handle = await lab.run_rev(revs["healthy"])
    restarted = Lab(folder / "shop", revs["healthy"], folder / "lab")
    assert (await restarted.replay(revs["broken"], REQUEST))["status"] == 500
    await lab._process.wait()
    with pytest.raises(ProcessLookupError):
        os.kill(handle.pid, 0)


async def test_dependency_link_is_never_committed(lab, revs):
    path, _ = await lab.candidate_branch(revs["healthy"], "inc-3", 1)
    (path / ".gitignore").unlink()
    sha = await lab.commit(path, "Drop gitignore")
    assert (await lab.git("diff", "--name-only", revs["healthy"], sha)).split() == [".gitignore"]


async def test_boot_crash_and_unknown_revision(lab, revs):
    path, _ = await lab.candidate_branch(revs["healthy"], "inc-2", 1)
    (path / "src" / "server.js").write_text("throw new Error('boom');\n")
    crashed = await lab.replay(await lab.commit(path, "Break boot"), REQUEST)
    assert crashed["status"] == 0 and "boom" in crashed["body_excerpt"]
    with pytest.raises(LabError):
        await lab.resolve("--help")
