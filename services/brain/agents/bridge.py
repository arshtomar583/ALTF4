"""Adapt the AI layer to the shared event store.

Uses EventStore.emit when it exists. Before that, events go straight into the
shared events table. Agent checkpoints live in agent_steps in the same database.
"""

import json
import os
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import event_store


def db_path() -> Path:
    return Path(os.getenv("PHOENIX_DB_PATH", ".data/phoenix.sqlite3"))


def store() -> event_store.EventStore:
    store = event_store.EventStore(db_path())
    if not db_path().exists():
        store.initialize()
    return store


def _connect() -> sqlite3.Connection:
    store()
    db = sqlite3.connect(db_path())
    db.row_factory = sqlite3.Row
    db.execute(
        """CREATE TABLE IF NOT EXISTS agent_steps (
            incident_id TEXT NOT NULL,
            key TEXT NOT NULL,
            output TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            PRIMARY KEY (incident_id, key)
        )"""
    )
    return db


async def emit(type: str, *, incident_id: str, actor: str, summary: str, data: dict | None = None) -> None:
    data = data or {}
    if team_emit := getattr(store(), "emit", None):
        team_emit(type, data, actor=actor, summary=summary, incident_id=incident_id)
        return
    payload = {"incident_id": incident_id, "actor": actor, "summary": summary, **data}
    with closing(_connect()) as db, db:
        db.execute(
            "INSERT INTO events (type, occurred_at, payload) VALUES (?, ?, ?)",
            (type, datetime.now(timezone.utc).isoformat(), json.dumps(payload)),
        )


async def step(incident_id: str, key: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run fn once per (incident, key). fn must return JSON-serialisable output."""
    if (done := saved_step(incident_id, key)) is not None:
        return done
    output = json.dumps(await fn())
    with closing(_connect()) as db, db:
        db.execute(
            "INSERT OR REPLACE INTO agent_steps VALUES (?, ?, ?, ?)",
            (incident_id, key, output, datetime.now(timezone.utc).isoformat()),
        )
    return json.loads(output)


def saved_step(incident_id: str, key: str) -> Any:
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT output FROM agent_steps WHERE incident_id = ? AND key = ?", (incident_id, key)
        ).fetchone()
    return json.loads(row["output"]) if row else None


def latest_step(key: str) -> Any:
    """The newest saved output for a key, across incidents."""
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT output FROM agent_steps WHERE key = ? ORDER BY completed_at DESC LIMIT 1", (key,)
        ).fetchone()
    return json.loads(row["output"]) if row else None


def events() -> list[dict]:
    """Every event in the team shape, whichever store wrote it."""
    with closing(_connect()) as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY id")]
    result = []
    for row in rows:
        data = json.loads(row["payload"])
        event = {key: row[key] for key in ("id", "type", "occurred_at")}
        for key in ("incident_id", "actor", "summary"):
            event[key] = row[key] if row.get(key) is not None else data.get(key)
            if key not in row:
                data.pop(key, None)
        result.append(event | {"data": data})
    return result
