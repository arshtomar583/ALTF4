"""Persist probe events for the dashboard and coordinator."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel


class CheckoutProbe(BaseModel):
    ok: bool
    status_code: int | None
    latency_ms: int
    slot: str | None
    revision: str | None


class EventStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY,
                    type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )"""
            )

    def record_probe(self, probe: CheckoutProbe) -> None:
        occurred_at = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO events (type, occurred_at, payload) VALUES (?, ?, ?)",
                ("checkout.probe", occurred_at, probe.model_dump_json()),
            )

    def latest_id(self) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    def after(self, event_id: int) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT id, type, occurred_at, payload FROM events WHERE id > ? ORDER BY id LIMIT 100",
                (event_id,),
            ).fetchall()
        return [
            {"id": id_, "type": type_, "occurred_at": time, "data": json.loads(payload)}
            for id_, type_, time, payload in rows
        ]
