"""Connect the AI layer to the shared event store.

Agent checkpoints live in agent_steps, in the same database. The store's own
steps table has no unique key per step and only accepts incidents it opened.
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
    store().emit(type, data or {}, actor=actor, summary=summary, incident_id=incident_id)


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


def last_step(incident_id: str) -> str | None:
    """The agent step this incident completed most recently."""
    with closing(_connect()) as db:
        row = db.execute(
            """SELECT key FROM agent_steps WHERE incident_id = ? AND key != 'context'
            ORDER BY completed_at DESC LIMIT 1""",
            (incident_id,),
        ).fetchone()
    return row["key"] if row else None


def latest_step(key: str) -> Any:
    """The newest saved output for a key, across incidents."""
    with closing(_connect()) as db:
        row = db.execute(
            "SELECT output FROM agent_steps WHERE key = ? ORDER BY completed_at DESC LIMIT 1", (key,)
        ).fetchone()
    return json.loads(row["output"]) if row else None


def events() -> list[dict]:
    """Every recorded event, oldest first."""
    with closing(_connect()) as db:
        rows = db.execute(
            "SELECT id, type, occurred_at, incident_id, actor, summary, payload FROM events ORDER BY id"
        ).fetchall()
    result = []
    for row in map(dict, rows):
        data = json.loads(row.pop("payload"))
        result.append(row | {"data": data})
    return result
