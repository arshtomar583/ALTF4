"""Keep checkout routing independent from the incident coordinator."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

Slot = Literal["a", "b"]


class SwitchState(BaseModel):
    active_slot: Slot


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.routing = SwitchState(active_slot=os.getenv("PHOENIX_START_SLOT", "a"))
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
