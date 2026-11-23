"""Keep checkout routing independent from the incident coordinator."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

Slot = Literal["a", "b"]


class SwitchState(BaseModel):
    active_slot: Slot


def save_state(path: Path, state: SwitchState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(state.model_dump_json())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(path: Path, start_slot: str) -> SwitchState:
    try:
        return SwitchState.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        state = SwitchState.model_validate({"active_slot": start_slot})
        save_state(path, state)
        return state


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    path = Path(os.getenv("PHOENIX_SWITCH_STATE_PATH", ".data/switch-state.json"))
    app.state.routing_path = path
    app.state.routing = load_state(path, os.getenv("PHOENIX_START_SLOT", "a"))
    app.state.targets = {
        "a": os.getenv("SHOP_A_URL", "http://127.0.0.1:3001"),
        "b": os.getenv("SHOP_B_URL", "http://127.0.0.1:3002"),
    }
    async with httpx.AsyncClient(timeout=1.5) as client:
        app.state.client = client
        yield


app = FastAPI(title="PHOENIX Version Switch", lifespan=lifespan)


@app.get("/state")
async def read_state() -> SwitchState:
    return app.state.routing


@app.put("/state")
async def update_state(state: SwitchState) -> SwitchState:
    save_state(app.state.routing_path, state)
    app.state.routing = state
    return state


@app.post("/checkout")
async def checkout(request: Request) -> Response:
    slot = app.state.routing.active_slot
    target = app.state.targets[slot]
    headers = {"X-Phoenix-Slot": slot}
    try:
        upstream = await app.state.client.post(
            f"{target}/checkout",
            content=await request.body(),
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
    except httpx.RequestError:
        return JSONResponse({"status": "upstream_unavailable"}, status_code=502, headers=headers)

    if revision := upstream.headers.get("x-shop-revision"):
        headers["X-Shop-Revision"] = revision
    headers["Content-Type"] = upstream.headers.get("content-type", "application/octet-stream")
    return Response(upstream.content, status_code=upstream.status_code, headers=headers)
