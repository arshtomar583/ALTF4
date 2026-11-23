"""Persist probe events for the dashboard and coordinator."""

import json
import re
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


class ApprovalRejected(ValueError):
    pass


class ReleaseRecord(BaseModel):
    id: int
    incident_id: str
    candidate_id: str
    revision: str
    previous_slot: Literal["a", "b"] | None = None
    rollback_reason: str | None = None


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
            db.execute(
                """CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY REFERENCES events(id),
                    incident_id TEXT NOT NULL REFERENCES incidents(id),
                    candidate_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    previous_slot TEXT CHECK (previous_slot IN ('a', 'b')),
                    rollback_reason TEXT,
                    finished_at TEXT
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

    def approve_candidate(self, incident_id: str, candidate_id: str) -> ReleaseRecord:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            incident = db.execute(
                "SELECT status, healthy_rev, broken_rev FROM incidents WHERE id = ? AND closed_at IS NULL",
                (incident_id,),
            ).fetchone()
            if incident is None:
                raise LookupError("Incident not found")
            if incident["status"] != "restored":
                raise ApprovalRejected("Only a restored incident can accept an approval")

            revision = self._verified_revision(db, incident_id, candidate_id)
            if revision in {incident["healthy_rev"], incident["broken_rev"]}:
                raise ApprovalRejected("The candidate must be a new fix revision")
            approval_id = self._insert_event(
                db,
                "approval.granted",
                {"candidate_id": candidate_id, "rev": revision},
                incident_id=incident_id,
                actor="release",
                summary=f"Approved candidate {candidate_id}",
            )
            db.execute(
                "INSERT INTO releases (id, incident_id, candidate_id, revision) VALUES (?, ?, ?, ?)",
                (approval_id, incident_id, candidate_id, revision),
            )
            db.execute("UPDATE incidents SET status = 'releasing' WHERE id = ?", (incident_id,))
        return ReleaseRecord(id=approval_id, incident_id=incident_id, candidate_id=candidate_id, revision=revision)

    @staticmethod
    def _verified_revision(db: sqlite3.Connection, incident_id: str, candidate_id: str) -> str:
        proposals = db.execute(
            """SELECT id, payload FROM events
            WHERE incident_id = ? AND type = 'candidate.proposed' AND json_extract(payload, '$.candidate_id') = ?
            ORDER BY id DESC""",
            (incident_id, candidate_id),
        ).fetchall()
        if not proposals:
            raise ApprovalRejected("No candidate was proposed for this incident")
        revisions = [json.loads(proposal["payload"]).get("rev") for proposal in proposals]
        revision = revisions[0]
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ApprovalRejected("Candidate proposals must identify a full Git commit SHA")
        if any(other != revision for other in revisions):
            raise ApprovalRejected("The candidate ID was reused for a different revision")

        verification = db.execute(
            """SELECT payload FROM events
            WHERE incident_id = ? AND type = 'verification.done' AND actor = 'verifier'
                AND json_extract(payload, '$.candidate_id') = ? AND id > ?
            ORDER BY id DESC LIMIT 1""",
            (incident_id, candidate_id, proposals[0]["id"]),
        ).fetchone()
        if verification is None:
            raise ApprovalRejected("The candidate requires verification after its latest proposal")
        result = json.loads(verification["payload"])
        if result.get("verdict") != "VERIFIED" or result.get("rev", revision) != revision:
            raise ApprovalRejected("The independent verifier did not approve this candidate revision")
        return revision

    def pending_release(self, incident_id: str) -> ReleaseRecord | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT id, incident_id, candidate_id, revision, previous_slot, rollback_reason
                FROM releases WHERE incident_id = ? AND finished_at IS NULL ORDER BY id DESC LIMIT 1""",
                (incident_id,),
            ).fetchone()
        return None if row is None else ReleaseRecord.model_validate(dict(row))

    def plan_release(self, release_id: int, previous_slot: Literal["a", "b"]) -> bool:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE releases SET previous_slot = ? WHERE id = ? AND previous_slot IS NULL AND finished_at IS NULL",
                (previous_slot, release_id),
            )
            return changed.rowcount == 1

    def require_release_rollback(self, release_id: int, reason: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE releases SET rollback_reason = ? WHERE id = ? AND finished_at IS NULL",
                (reason, release_id),
            )

    def finish_release(
        self,
        incident: IncidentRecord,
        release: ReleaseRecord,
        *,
        active_slot: Literal["a", "b"],
        active_rev: str,
        failure: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        ok = failure is None
        data = {
            "candidate_id": release.candidate_id,
            "ok": ok,
            "active_slot": active_slot,
            "active_rev": active_rev,
            "seconds_to_verified_fix": round((now - datetime.fromisoformat(incident.opened_at)).total_seconds(), 3)
            if ok else None,
            "reason": failure,
        }
        with self._connect() as db:
            changed = db.execute(
                "UPDATE releases SET finished_at = ? WHERE id = ? AND finished_at IS NULL",
                (now.isoformat(), release.id),
            )
            if not changed.rowcount:
                return
            db.execute(
                "UPDATE incidents SET status = ? WHERE id = ? AND status = 'releasing'",
                ("released" if ok else "restored", incident.id),
            )
            db.execute(
                "INSERT INTO steps (incident_id, step, completed_at, payload) VALUES (?, ?, ?, ?)",
                (incident.id, "release", now.isoformat(), json.dumps(data)),
            )
            self._insert_event(
                db,
                "release.done",
                data,
                incident_id=incident.id,
                actor="release",
                summary="Verified checkout fix released" if ok else "Candidate release stopped",
            )
