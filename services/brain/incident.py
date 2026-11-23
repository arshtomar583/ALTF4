"""Detect checkout failures and restore a previously healthy revision."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from .checkout import PLAIN_CART, check_charge
from .event_store import EventStore, IncidentRecord

Slot = Literal["a", "b"]
IncidentHook = Callable[[IncidentRecord], Awaitable[None]]
logger = logging.getLogger(__name__)
STARTER_CART = PLAIN_CART


class RecoveryError(RuntimeError):
    pass


class SlotSnapshot(BaseModel):
    active_slot: Slot
    healthy_rev: str
    revisions: dict[Slot, str]


class ProbeFailures:
    def __init__(self) -> None:
        self.target: tuple[str | None, str | None] | None = None
        self.counts: dict[bool, int] = {}

    def observe(self, probe: dict[str, Any]) -> bool:
        target = (probe["slot"], probe["revision"])
        if self.target != target:
            self.target = target
            self.counts.clear()
        discount = probe["discount"]
        self.counts[discount] = 0 if probe["ok"] else self.counts.get(discount, 0) + 1
        return self.counts[discount] >= 3


class IncidentEngine:
    def __init__(self, store: EventStore, switch: httpx.AsyncClient, bad_revision: str | None):
        self.store = store
        self.switch = switch
        self.bad_revision = bad_revision
        self.operation = asyncio.Lock()
        self.on_restored: list[IncidentHook] = []
        self.on_released: list[IncidentHook] = []
        self.hook_tasks: set[asyncio.Task] = set()

    def notify(self, hooks: list[IncidentHook], incident: IncidentRecord) -> None:
        """Run hooks in the background, so they never hold up recovery or a release."""
        for hook in hooks:
            task = asyncio.create_task(hook(incident))
            self.hook_tasks.add(task)
            task.add_done_callback(self.hook_tasks.discard)

    async def snapshot(self) -> SlotSnapshot:
        response = await self.switch.get("/slots")
        return SlotSnapshot.model_validate(response.raise_for_status().json())

    async def deploy(self, slot: Slot, revision: str, expected_active_slot: Slot) -> None:
        response = await self.switch.post(
            f"/slots/{slot}/deploy",
            json={"revision": revision, "expected_active_slot": expected_active_slot},
            timeout=40,
        )
        response.raise_for_status()

    async def switch_to(self, slot: Slot, expected_active_slot: Slot) -> None:
        response = await self.switch.put(
            "/state", json={"active_slot": slot, "expected_active_slot": expected_active_slot}
        )
        response.raise_for_status()

    async def promote(self, slot: Slot, revision: str) -> None:
        response = await self.switch.put(f"/slots/{slot}/healthy", json={"revision": revision})
        response.raise_for_status()

    async def checkout_passes(self, path: str, expected_slot: Slot, revision: str, cart: dict[str, Any]) -> bool:
        response = await self.switch.post(path, json=cart)
        if not (
            response.is_success
            and response.headers.get("x-phoenix-slot") == expected_slot
            and response.headers.get("x-shop-revision") == revision
        ):
            return False
        ok, _ = check_charge(response, cart)
        return ok

    async def break_checkout(self) -> None:
        if not self.bad_revision:
            raise RecoveryError("Set PHOENIX_BAD_REV to the commit containing the checkout fault")
        async with self.operation:
            if self.store.current_incident() is not None:
                raise RecoveryError("An incident is already open")
            state = await self.snapshot()
            active = state.active_slot
            if self.bad_revision == state.healthy_rev:
                raise RecoveryError("The fault must use a different commit from the healthy revision")
            if not await self.checkout_passes("/checkout", active, state.healthy_rev, STARTER_CART):
                raise RecoveryError("Checkout must pass on the healthy revision before breaking it")
            self.store.emit("chaos.break", {}, actor="chaos", summary="Checkout break requested")
            await self.deploy(active, self.bad_revision, active)

    async def observe_failure(self, data: dict[str, Any]) -> None:
        body = data.get("request_body")
        if body is None:
            if data["discount"]:
                return
            body = STARTER_CART
        async with self.operation:
            if self.store.current_incident() is not None:
                return
            state = await self.snapshot()
            active = state.active_slot
            if (data["slot"], data["revision"]) != (active, state.revisions[active]):
                return
            self.store.open_incident(
                broken_slot=active,
                broken_rev=state.revisions[active],
                healthy_rev=state.healthy_rev,
                failing_request={"method": "POST", "path": "/checkout", "body": body},
            )

    async def restore(self, incident: IncidentRecord) -> None:
        async with self.operation:
            state = await self.snapshot()
            active = state.active_slot
            healthy = incident.healthy_rev
            cart = incident.failing_request.get("body", incident.failing_request)
            if state.revisions[active] == healthy and await self.checkout_passes("/checkout", active, healthy, cart):
                self.store.mark_restored(incident, active)
                return
            if active != incident.broken_slot:
                raise RecoveryError("The active slot changed while checkout was being restored")

            idle: Slot = "b" if active == "a" else "a"
            if state.revisions[idle] != healthy:
                await self.deploy(idle, healthy, active)
            if not await self.checkout_passes(f"/slots/{idle}/checkout", idle, healthy, cart):
                raise RecoveryError("The healthy slot failed checkout verification")

            await self.switch_to(idle, active)
            try:
                if not await self.checkout_passes("/checkout", idle, healthy, cart):
                    raise RecoveryError("Checkout failed after switching traffic")
            except (httpx.HTTPError, RecoveryError):
                await self.switch_to(active, idle)
                raise
            self.store.mark_restored(incident, idle)

    async def watch(self, cursor: int) -> None:
        streaks = ProbeFailures()
        while True:
            incident = self.store.current_incident()
            if incident is not None and incident.status == "open":
                try:
                    await self.restore(incident)
                    self.notify(self.on_restored, incident)
                except (httpx.HTTPError, RecoveryError) as error:
                    logger.warning("Incident %s restore pending: %s", incident.id, error)
                    await asyncio.sleep(1)
            for event in self.store.after(cursor):
                cursor = event["id"]
                if event["type"] == "checkout.probe" and streaks.observe(event["data"]):
                    try:
                        await self.observe_failure(event["data"])
                    except httpx.HTTPError as error:
                        logger.warning("Unable to check the slot for failed probe %s: %s", cursor, error)
            await asyncio.sleep(0.25)
