"""Persist probe events for the dashboard and coordinator."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel


class CheckoutProbe(BaseModel):
    ok: bool
    status_code: int | None
    latency_ms: int
    slot: str | None
    revision: str | None
    discount: bool = False
    cart_total_cents: int | None = None
    request_body: dict[str, Any] | None = None


class IncidentRecord(BaseModel):
    id: str
    status: str
    opened_at: str
    broken_rev: str
    broken_slot: Literal["a", "b"]
    healthy_rev: str
    failing_request: dict[str, Any]
    closed_at: str | None


def checkout_summary(ok: bool) -> str:
    return "Checkout passed" if ok else "Checkout failed"


class EventStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            yield db

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    incident_id TEXT,
                    actor TEXT NOT NULL DEFAULT 'probe',
                    summary TEXT
                )"""
            )
            # Preserve IDs and probe history in databases from the starter.
            columns = {column["name"] for column in db.execute("PRAGMA table_info(events)")}
            for name, declaration in (
                ("incident_id", "TEXT"),
                ("actor", "TEXT NOT NULL DEFAULT 'probe'"),
                ("summary", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE events ADD COLUMN {name} {declaration}")
            db.execute(
                """CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    opened_at TEXT NOT NULL,
                    broken_rev TEXT NOT NULL,
                    broken_slot TEXT NOT NULL CHECK (broken_slot IN ('a', 'b')),
                    healthy_rev TEXT NOT NULL,
                    failing_request TEXT NOT NULL,
                    closed_at TEXT
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS steps (
                    id INTEGER PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    step TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}'
                )"""
            )

    def emit(
        self,
        event_type: str,
        data: dict[str, Any],
        *,
        actor: str,
        summary: str,
        incident_id: str | None = None,
    ) -> int:
        with self._connect() as db:
            return self._insert_event(db, event_type, data, actor=actor, summary=summary, incident_id=incident_id)

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        event_type: str,
        data: dict[str, Any],
        *,
        actor: str,
        summary: str,
        incident_id: str | None,
    ) -> int:
        cursor = db.execute(
            """INSERT INTO events (type, occurred_at, payload, incident_id, actor, summary)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (event_type, datetime.now(timezone.utc).isoformat(), json.dumps(data), incident_id, actor, summary),
        )
        event_id = cursor.lastrowid
        assert event_id is not None
        return event_id

    def record_probe(self, probe: CheckoutProbe) -> None:
        self.emit(
            "checkout.probe",
            probe.model_dump(mode="json", exclude={"request_body"} if probe.request_body is None else set()),
            actor="probe",
            summary=checkout_summary(probe.ok),
        )

    def latest_id(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def after(self, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT id, type, occurred_at, incident_id, actor, summary, payload
                FROM events WHERE id > ? ORDER BY id LIMIT 100""",
                (event_id,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            data = json.loads(event.pop("payload"))
            if event["type"] == "checkout.probe":
                data.setdefault("discount", False)
                data.setdefault("cart_total_cents", None)
                if event["summary"] is None:
                    event["summary"] = checkout_summary(data["ok"])
            event["data"] = data
            events.append(event)
        return events

    def current_incident(self) -> IncidentRecord | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id, status, opened_at, broken_rev, broken_slot, healthy_rev, failing_request, closed_at
                FROM incidents
                WHERE closed_at IS NULL
                ORDER BY opened_at DESC, id DESC
                LIMIT 1"""
            ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["failing_request"] = json.loads(record["failing_request"])
        return IncidentRecord.model_validate(record)

    def open_incident(
        self,
        *,
        broken_slot: Literal["a", "b"],
        broken_rev: str,
        healthy_rev: str,
        failing_request: dict[str, Any],
    ) -> None:
        incident_id = f"inc_{uuid4().hex}"
        opened_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM incidents WHERE closed_at IS NULL LIMIT 1").fetchone():
                return
            db.execute(
                """INSERT INTO incidents (id, opened_at, broken_rev, broken_slot, healthy_rev, failing_request)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (incident_id, opened_at, broken_rev, broken_slot, healthy_rev, json.dumps(failing_request)),
            )
            self._insert_event(
                db,
                "incident.opened",
                {
                    "broken_slot": broken_slot,
                    "broken_rev": broken_rev,
                    "healthy_rev": healthy_rev,
                    "failing_request": failing_request,
                },
                incident_id=incident_id,
                actor="coordinator",
                summary=f"Checkout incident opened on slot {broken_slot.upper()}",
            )

    def mark_restored(self, incident: IncidentRecord, active_slot: Literal["a", "b"]) -> None:
        now = datetime.now(timezone.utc)
        elapsed = max(0, (now - datetime.fromisoformat(incident.opened_at)).total_seconds())
        data = {
            "active_slot": active_slot,
            "active_rev": incident.healthy_rev,
            "seconds_to_restore": round(elapsed, 3),
        }
        with self._connect() as db:
            updated = db.execute(
                "UPDATE incidents SET status = 'restored' WHERE id = ? AND status = 'open' AND closed_at IS NULL",
                (incident.id,),
            )
            if not updated.rowcount:
                return
            db.execute(
                "INSERT INTO steps (incident_id, step, completed_at, payload) VALUES (?, ?, ?, ?)",
                (incident.id, "restore", now.isoformat(), json.dumps(data)),
            )
            self._insert_event(
                db,
                "incident.restored",
                data,
                incident_id=incident.id,
                actor="coordinator",
                summary=f"Restored checkout on slot {active_slot.upper()}",
            )
