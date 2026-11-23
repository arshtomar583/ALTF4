import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from brain.event_store import ApprovalRejected, EventStore
from brain.incident import IncidentEngine
from brain.release import ReleaseRunner

BROKEN = "0" * 40
HEALTHY = "1" * 40
CANDIDATE = "2" * 40
CART = {"sku": "demo-mug", "quantity": 1}


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = EventStore(Path(directory.name) / "events.sqlite3")
        self.store.initialize()
        self.store.open_incident(
            broken_slot="a",
            broken_rev=BROKEN,
            healthy_rev=HEALTHY,
            failing_request={"method": "POST", "path": "/checkout", "body": CART},
        )
        self.store.mark_restored(self.store.current_incident(), "b")
        self.incident_id = self.store.current_incident().id

    def propose(self, revision=CANDIDATE):
        self.store.emit(
            "candidate.proposed", {"candidate_id": "c1", "rev": revision},
            incident_id=self.incident_id, actor="repair", summary="Candidate proposed",
        )

    def verify(self, *, actor="verifier", verdict="VERIFIED", revision=CANDIDATE):
        self.store.emit(
            "verification.done", {"candidate_id": "c1", "rev": revision, "verdict": verdict},
            incident_id=self.incident_id, actor=actor, summary="Verification complete",
        )

    def approve(self):
        return self.store.approve_candidate(self.incident_id, "c1")

    def test_approval_requires_the_verifiers_latest_verdict_for_this_commit(self):
        self.propose()
        self.verify(actor="repair")
        with self.assertRaises(ApprovalRejected):
            self.approve()
        self.verify(revision="3" * 40)
        with self.assertRaises(ApprovalRejected):
            self.approve()
        self.verify()
        self.verify(verdict="REJECTED")
        with self.assertRaises(ApprovalRejected):
            self.approve()
        self.verify()
        release = self.approve()
        self.assertEqual(release.revision, CANDIDATE)
        self.assertEqual(self.store.current_incident().status, "releasing")
        with self.assertRaises(ApprovalRejected):
            self.approve()
        approvals = [event for event in self.store.after(0) if event["type"] == "approval.granted"]
        self.assertEqual([event["data"] for event in approvals], [{"candidate_id": "c1", "rev": CANDIDATE}])

    def test_reusing_candidate_id_cannot_carry_over_verification(self):
        self.propose()
        self.verify()
        self.propose()
        with self.assertRaisesRegex(ApprovalRejected, "latest proposal"):
            self.approve()
        self.propose("3" * 40)
        self.verify(revision="3" * 40)
        with self.assertRaisesRegex(ApprovalRejected, "reused"):
            self.approve()

    def test_known_broken_revision_cannot_be_approved(self):
        self.propose(BROKEN)
        self.verify(revision=BROKEN)
        with self.assertRaisesRegex(ApprovalRejected, "new fix revision"):
            self.approve()


class ReleaseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = EventStore(Path(directory.name) / "events.sqlite3")
        self.store.initialize()
        self.store.open_incident(
            broken_slot="a",
            broken_rev=BROKEN,
            healthy_rev=HEALTHY,
            failing_request={"method": "POST", "path": "/checkout", "body": CART},
        )
        self.store.mark_restored(self.store.current_incident(), "b")
        incident_id = self.store.current_incident().id
        self.store.emit(
            "candidate.proposed", {"candidate_id": "c1", "rev": CANDIDATE},
            incident_id=incident_id, actor="repair", summary="Candidate proposed",
        )
        self.store.emit(
            "verification.done", {"candidate_id": "c1", "verdict": "VERIFIED"},
            incident_id=incident_id, actor="verifier", summary="Verified candidate",
        )
        self.release = self.store.approve_candidate(incident_id, "c1")
        self.active = "b"
        self.healthy = HEALTHY
        self.revisions = {"a": BROKEN, "b": HEALTHY}
        self.calls = []
        self.preflight_passes = True
        self.live_candidate_passes = True

    def response(self, request):
        route = request.url.path
        if route == "/slots":
            return httpx.Response(
                200, json={"active_slot": self.active, "healthy_rev": self.healthy, "revisions": self.revisions}
            )
        body = json.loads(request.content)
        if route == "/slots/a/deploy":
            assert body == {"revision": CANDIDATE, "expected_active_slot": "b"}
            self.revisions["a"] = CANDIDATE
            self.calls.append("deploy a")
            return httpx.Response(200, json={"slot": "a", "revision": CANDIDATE})
        if route == "/state":
            assert body["expected_active_slot"] == self.active
            self.active = body["active_slot"]
            self.calls.append(f"switch {self.active}")
            return httpx.Response(200, json={"active_slot": self.active})
        if route == f"/slots/{self.active}/healthy":
            assert body["revision"] == self.revisions[self.active]
            self.healthy = body["revision"]
            self.calls.append(f"promote {self.active}")
            return httpx.Response(200, json={"healthy_rev": self.healthy})
        assert body == CART
        if route == "/slots/a/checkout":
            slot, passed = "a", self.preflight_passes
            self.calls.append("preflight a")
        else:
            assert route == "/checkout"
            slot = self.active
            passed = slot == "b" or self.live_candidate_passes
            self.calls.append(f"checkout {slot}")
        return httpx.Response(
            200,
            headers={"X-Phoenix-Slot": slot, "X-Shop-Revision": self.revisions[slot]},
            json={"status": "accepted" if passed else "rejected", "totalCents": 1200},
        )

    async def run_release(self):
        transport = httpx.MockTransport(self.response)
        async with httpx.AsyncClient(base_url="http://switch", transport=transport) as client:
            runner = ReleaseRunner(IncidentEngine(self.store, client, None))
            await runner.run(self.store.current_incident(), self.store.pending_release(self.release.incident_id))

    def completion(self):
        (event,) = (event for event in self.store.after(0) if event["type"] == "release.done")
        return event["data"]

    async def test_release_checks_checkout_three_times_on_each_route(self):
        await self.run_release()
        self.assertEqual(
            self.calls,
            ["checkout b", "deploy a", *["preflight a"] * 3, "switch a", *["checkout a"] * 3, "promote a"],
        )
        self.assertIsNone(self.store.current_incident())
        self.assertEqual(self.healthy, CANDIDATE)
        self.assertTrue(self.completion()["ok"])

    async def test_preflight_failure_keeps_the_restored_slot_live(self):
        self.preflight_passes = False
        await self.run_release()
        self.assertEqual(self.active, "b")
        self.assertEqual(self.healthy, HEALTHY)
        self.assertNotIn("switch a", self.calls)
        self.assertEqual(self.store.current_incident().status, "restored")
        self.assertFalse(self.completion()["ok"])

    async def test_failed_live_checkout_switches_back(self):
        self.live_candidate_passes = False
        await self.run_release()
        self.assertEqual(self.calls[-4:], ["switch a", "checkout a", "switch b", "checkout b"])
        self.assertEqual(self.active, "b")
        self.assertEqual(self.healthy, HEALTHY)
        self.assertEqual(self.completion()["active_rev"], HEALTHY)
        self.assertFalse(self.completion()["ok"])

    async def test_restart_after_switch_verifies_existing_live_candidate(self):
        self.store.plan_release(self.release.id, "b")
        self.active = "a"
        self.revisions["a"] = CANDIDATE
        await self.run_release()
        self.assertEqual(self.calls, ["checkout a", "checkout a", "checkout a", "promote a"])
        self.assertTrue(self.completion()["ok"])

    async def test_restart_after_rollback_does_not_redeploy_failed_candidate(self):
        self.store.plan_release(self.release.id, "b")
        self.store.require_release_rollback(self.release.id, "Candidate failed live checkout")
        self.revisions["a"] = CANDIDATE
        self.healthy = CANDIDATE
        await self.run_release()
        self.assertEqual(self.calls, ["checkout b", "promote b"])
        self.assertEqual(self.healthy, HEALTHY)
        self.assertFalse(self.completion()["ok"])
