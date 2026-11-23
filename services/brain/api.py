"""Start the checkout probe and stream persisted events."""

import asyncio
import json
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .event_store import EventStore, IncidentRecord
from .incident import IncidentEngine, RecoveryError
from .probe import poll_checkout


class BreakRequest(BaseModel):
    fault: Literal["bad_commit"]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = EventStore(Path(os.getenv("PHOENIX_DB_PATH", ".data/phoenix.sqlite3")))
    store.initialize()
    app.state.store = store
    checkout_url = os.getenv("PHOENIX_CHECKOUT_URL", "http://127.0.0.1:8080/checkout")
    interval = float(os.getenv("PHOENIX_PROBE_INTERVAL_SECONDS", "2"))
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("PHOENIX_PROBE_INTERVAL_SECONDS must be positive")
    switch_url = os.getenv("PHOENIX_SWITCH_URL")
    async with httpx.AsyncClient(base_url=switch_url or "http://127.0.0.1:8080", timeout=3) as client:
        app.state.engine = None
        workers = [asyncio.create_task(poll_checkout(store, checkout_url, interval))]
        if switch_url:
            engine = IncidentEngine(store, client, os.getenv("PHOENIX_BAD_REV"))
            app.state.engine = engine
            workers.append(asyncio.create_task(engine.watch(store.latest_id())))
        try:
            yield
        finally:
            for worker in workers:
                worker.cancel()
            for worker in workers:
                with suppress(asyncio.CancelledError):
                    await worker


app = FastAPI(title="PHOENIX Incident API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Last-Event-ID", "Content-Type"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/incidents/current")
async def current_incident() -> IncidentRecord | None:
    store: EventStore = app.state.store
    return store.current_incident()


@app.post("/chaos/break")
async def break_checkout(_request: BreakRequest) -> dict[str, str]:
    engine: IncidentEngine | None = app.state.engine
    if engine is None:
        raise HTTPException(status_code=503, detail="The Git version switch is not configured")
    try:
        await engine.break_checkout()
    except RecoveryError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="The version switch could not deploy the checkout fault") from error
    return {"status": "injected"}


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
