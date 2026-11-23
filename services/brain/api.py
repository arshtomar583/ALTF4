"""Start the checkout probe and stream persisted events."""

import asyncio
import json
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .event_store import EventStore
from .probe import poll_checkout


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = EventStore(Path(os.getenv("PHOENIX_DB_PATH", ".data/phoenix.sqlite3")))
    store.initialize()
    app.state.store = store
    checkout_url = os.getenv("PHOENIX_CHECKOUT_URL", "http://127.0.0.1:8080/checkout")
    interval = float(os.getenv("PHOENIX_PROBE_INTERVAL_SECONDS", "2"))
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("PHOENIX_PROBE_INTERVAL_SECONDS must be positive")
    worker = asyncio.create_task(poll_checkout(store, checkout_url, interval))
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="PHOENIX Incident API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_headers=["Last-Event-ID"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/events", response_class=StreamingResponse)
async def events(
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> StreamingResponse:
    store = app.state.store
    latest = store.latest_id()
    cursor = max(0, latest - 30)
    if last_event_id is not None and last_event_id <= latest:
        cursor = last_event_id
    return StreamingResponse(
        stream_events(store, cursor),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def stream_events(store: EventStore, cursor: int) -> AsyncIterator[str]:
    while True:
        batch = store.after(cursor)
        for event in batch:
            cursor = event["id"]
            yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
        if not batch:
            await asyncio.sleep(0.5)
