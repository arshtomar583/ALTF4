"""Keep checkout routing independent from the incident coordinator."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .revisions import RevisionError, Slot, SlotRevisions


class SwitchState(BaseModel):
    active_slot: Slot


class SwitchChange(SwitchState):
    expected_active_slot: Slot | None = None


class Deployment(BaseModel):
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    expected_active_slot: Slot


class Promotion(BaseModel):
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


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
    app.state.mutation_lock = asyncio.Lock()
    app.state.revisions = None
    if root := os.getenv("PHOENIX_REVISIONS_PATH"):
        revisions = SlotRevisions(Path(root), Path(os.environ["PHOENIX_SOURCE_GIT"]))
        await asyncio.to_thread(revisions.initialize)
        app.state.revisions = revisions
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
async def update_state(change: SwitchChange) -> SwitchState:
    async with app.state.mutation_lock:
        verify_active_slot(change.expected_active_slot)
        state = SwitchState(active_slot=change.active_slot)
        save_state(app.state.routing_path, state)
        app.state.routing = state
        return state


def verify_active_slot(expected: Slot | None) -> None:
    if expected is not None and app.state.routing.active_slot != expected:
        raise HTTPException(status_code=409, detail="The active slot changed")


def revision_store() -> SlotRevisions:
    revisions = app.state.revisions
    if revisions is None:
        raise HTTPException(status_code=503, detail="Git revisions are not configured")
    return revisions


@app.get("/slots")
async def slots() -> dict:
    revisions = revision_store()
    return {
        "active_slot": app.state.routing.active_slot,
        "healthy_rev": revisions.healthy_revision(),
        "revisions": {slot: revisions.revision(slot) for slot in ("a", "b")},
    }


@app.post("/slots/{slot}/deploy")
async def deploy(slot: Slot, deployment: Deployment) -> dict[str, str]:
    revisions = revision_store()
    async with app.state.mutation_lock:
        verify_active_slot(deployment.expected_active_slot)
        try:
            await asyncio.to_thread(revisions.checkout_rev, slot, deployment.revision)
        except RevisionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        for _ in range(20):
            try:
                upstream = await app.state.client.get(f"{app.state.targets[slot]}/health", timeout=0.5)
                if upstream.is_success and upstream.headers.get("x-shop-revision") == deployment.revision:
                    return {"slot": slot, "revision": deployment.revision}
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.15)
        raise HTTPException(status_code=503, detail=f"Slot {slot} did not start the requested revision")


@app.put("/slots/{slot}/healthy")
async def promote(slot: Slot, promotion: Promotion) -> dict[str, str]:
    revisions = revision_store()
    async with app.state.mutation_lock:
        verify_active_slot(slot)
        try:
            await asyncio.to_thread(revisions.promote, slot, promotion.revision)
        except RevisionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    return {"healthy_rev": promotion.revision}


@app.post("/checkout")
async def checkout(request: Request) -> Response:
    return await forward_checkout(app.state.routing.active_slot, request)


@app.post("/slots/{slot}/checkout")
async def checkout_slot(slot: Slot, request: Request) -> Response:
    return await forward_checkout(slot, request)


async def forward_checkout(slot: Slot, request: Request) -> Response:
    target = app.state.targets[slot]
    headers = {"X-Phoenix-Slot": slot}
    try:
        upstream = await app.state.client.post(
            f"{target}/checkout",
            content=await request.body(),
            headers={"Content-Type": request.headers.get("content-type", "application/json")},
        )
    except httpx.RequestError:
        if app.state.revisions is not None:
            headers["X-Shop-Revision"] = app.state.revisions.revision(slot)
        return JSONResponse({"status": "upstream_unavailable"}, status_code=502, headers=headers)

    if revision := upstream.headers.get("x-shop-revision"):
        headers["X-Shop-Revision"] = revision
    headers["Content-Type"] = upstream.headers.get("content-type", "application/octet-stream")
    return Response(upstream.content, status_code=upstream.status_code, headers=headers)
