import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from brain.event_store import EventStore
from brain.incident import IncidentEngine, ProbeFailures, RecoveryError


class ProbeFailureTests(unittest.TestCase):
    def test_three_failures_of_one_cart_trigger_despite_other_cart_passing(self):
        failures = ProbeFailures()
        failed = {"slot": "a", "revision": "broken", "discount": False, "ok": False}
        passing = {**failed, "discount": True, "ok": True}
        self.assertFalse(failures.observe(failed))
        self.assertFalse(failures.observe(passing))
        self.assertFalse(failures.observe(failed))
        self.assertFalse(failures.observe(passing))
        self.assertTrue(failures.observe(failed))
        self.assertFalse(failures.observe({**failed, "revision": "another"}))


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = EventStore(Path(directory.name) / "events.sqlite3")
        self.store.initialize()
        self.cart = {"sku": "demo-mug", "quantity": 1}
        self.store.open_incident(
            broken_slot="a",
            broken_rev="broken",
            healthy_rev="healthy",
            failing_request={"path": "/checkout", "method": "POST", "body": self.cart},
        )
        self.active = "a"
        self.revisions = {"a": "broken", "b": "stale"}
        self.changes = []
        self.idle_passes = True
        self.charge = 1200
        self.switched_passes = True

    def response(self, request):
        route = request.url.path
        if route == "/slots":
            return httpx.Response(
                200, json={"active_slot": self.active, "healthy_rev": "healthy", "revisions": self.revisions}
            )
        body = json.loads(request.content)
        if route == "/slots/b/deploy":
            assert body == {"revision": "healthy", "expected_active_slot": "a"}
            self.revisions["b"] = "healthy"
            self.changes.append("deploy b")
            return httpx.Response(200, json={"slot": "b", "revision": "healthy"})
        if route == "/state":
            assert body["expected_active_slot"] == self.active
            self.active = body["active_slot"]
            self.changes.append(f"switch {self.active}")
            return httpx.Response(200, json={"active_slot": self.active})
        assert route in {"/slots/b/checkout", "/checkout"}
        assert body == self.cart
        slot = "b" if route == "/slots/b/checkout" else self.active
        passed = self.idle_passes if route == "/slots/b/checkout" else self.switched_passes
        return httpx.Response(
            200 if passed else 500,
            headers={"X-Phoenix-Slot": slot, "X-Shop-Revision": self.revisions[slot]},
            json={"status": "accepted" if passed else "failed", "totalCents": self.charge},
        )

    async def restore(self):
        async with httpx.AsyncClient(base_url="http://switch", transport=httpx.MockTransport(self.response)) as client:
            await IncidentEngine(self.store, client, None).restore(self.store.current_incident())

    async def test_failing_idle_revision_never_receives_live_traffic(self):
        self.idle_passes = False
        with self.assertRaisesRegex(RecoveryError, "failed checkout verification"):
            await self.restore()
        self.assertEqual(self.active, "a")
        self.assertEqual(self.changes, ["deploy b"])
        self.assertEqual(self.store.current_incident().status, "open")

    async def test_failed_post_switch_check_returns_to_previous_slot(self):
        self.switched_passes = False
        with self.assertRaisesRegex(RecoveryError, "failed after switching"):
            await self.restore()
        self.assertEqual(self.active, "a")
        self.assertEqual(self.changes, ["deploy b", "switch b", "switch a"])
        self.assertEqual(self.store.current_incident().status, "open")

    async def test_wrong_charge_on_healthy_slot_blocks_recovery(self):
        self.charge = 1
        with self.assertRaisesRegex(RecoveryError, "failed checkout verification"):
            await self.restore()
        self.assertEqual(self.active, "a")
        self.assertEqual(self.store.current_incident().status, "open")

    async def test_already_restored_traffic_is_verified_without_switching_again(self):
        self.active = "b"
        self.revisions["b"] = "healthy"
        await self.restore()
        self.assertEqual(self.changes, [])
        self.assertEqual(self.store.current_incident().status, "restored")
        self.assertEqual([event["type"] for event in self.store.after(0)], ["incident.opened", "incident.restored"])
