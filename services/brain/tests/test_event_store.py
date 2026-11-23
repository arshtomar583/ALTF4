import json
import os
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from brain.api import app
from brain.event_store import CheckoutProbe, EventStore


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "events.sqlite3"
        self.store = EventStore(self.path)

    def test_starter_probe_history_survives_schema_upgrade(self):
        payload = {"ok": False, "status_code": 500, "latency_ms": 8, "slot": "a", "revision": "starter-a"}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, type TEXT, occurred_at TEXT, payload TEXT)"
            )
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?)",
                (42, "checkout.probe", "2026-11-23T06:31:05Z", json.dumps(payload)),
            )

        self.store.initialize()
        self.store.initialize()
        event = self.store.after(0)[0]
        self.assertEqual(event["id"], 42)
        self.assertEqual(event["occurred_at"], "2026-11-23T06:31:05Z")
        self.assertIsNone(event["incident_id"])
        self.assertEqual(event["actor"], "probe")
        self.assertEqual(event["summary"], "Checkout failed")
        self.assertEqual(event["data"], {**payload, "discount": False, "cart_total_cents": None})

        self.store.record_probe(
            CheckoutProbe(ok=True, status_code=200, latency_ms=9, slot="b", revision="starter-b")
        )
        (probe_event,) = self.store.after(42)
        self.assertEqual(probe_event["id"], 43)
        self.assertEqual(probe_event["summary"], "Checkout passed")

    def test_emit_keeps_incident_metadata_separate_from_event_data(self):
        self.store.initialize()
        event_id = self.store.emit(
            "incident.restored",
            {"active_slot": "b", "active_rev": "7c01e9b"},
            incident_id="inc_3",
            actor="coordinator",
            summary="Restored checkout",
        )

        (event,) = self.store.after(event_id - 1)
        self.assertEqual(event["id"], event_id)
        self.assertEqual(event["type"], "incident.restored")
        self.assertEqual(event["incident_id"], "inc_3")
        self.assertEqual(event["actor"], "coordinator")
        self.assertEqual(event["summary"], "Restored checkout")
        self.assertEqual(event["data"], {"active_slot": "b", "active_rev": "7c01e9b"})

    def test_current_incident_endpoint_returns_open_record_or_null(self):
        request = {"sku": "demo-mug", "quantity": 1}
        with (
            patch.dict(os.environ, {"PHOENIX_DB_PATH": str(self.path)}),
            patch("brain.api.poll_checkout", new_callable=AsyncMock),
            TestClient(app) as client,
        ):
            response = client.get("/incidents/current")
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json())

            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute(
                    """INSERT INTO incidents (id, opened_at, broken_rev, broken_slot, healthy_rev, failing_request)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    ("inc_1", "2026-11-23T06:31:05Z", "a3f9c21", "a", "7c01e9b", json.dumps(request)),
                )
                db.execute(
                    "INSERT INTO steps (incident_id, step, completed_at) VALUES (?, ?, ?)",
                    ("inc_1", "restore", "2026-11-23T06:31:10Z"),
                )

            incident = client.get("/incidents/current").json()
            self.assertEqual(incident["id"], "inc_1")
            self.assertEqual(incident["failing_request"], request)

            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("UPDATE incidents SET closed_at = ? WHERE id = ?", ("2026-11-23T06:33:00Z", "inc_1"))
            self.assertIsNone(client.get("/incidents/current").json())
