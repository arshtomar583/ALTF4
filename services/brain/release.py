"""Publish a reviewed checkout revision with checkout checks on both routes."""

import asyncio
import logging
from typing import Any

import httpx

from .event_store import IncidentRecord, ReleaseRecord
from .incident import IncidentEngine, RecoveryError, Slot

logger = logging.getLogger(__name__)


class ReleaseRunner:
    def __init__(self, engine: IncidentEngine):
        self.engine = engine
        self.store = engine.store

    async def probes_pass(self, path: str, slot: Slot, revision: str, cart: dict[str, Any]) -> bool:
        try:
            for _ in range(3):
                if not await self.engine.checkout_passes(path, slot, revision, cart):
                    return False
        except httpx.RequestError:
            return False
        return True

    async def run(self, incident: IncidentRecord, release: ReleaseRecord) -> None:
        async with self.engine.operation:
            state = await self.engine.snapshot()
            active = state.active_slot
            cart = incident.failing_request.get("body", incident.failing_request)
            previous = release.previous_slot

            if previous is None:
                if state.revisions[active] != incident.healthy_rev:
                    raise RecoveryError("The active revision differs from the restored revision")
                if not await self.engine.checkout_passes("/checkout", active, incident.healthy_rev, cart):
                    raise RecoveryError("The restored checkout is currently failing")
                if not self.store.plan_release(release.id, active):
                    return
                previous = active

            target: Slot = "b" if previous == "a" else "a"
            if release.rollback_reason:
                await self.rollback(incident, release, previous, target, cart, release.rollback_reason)
                return

            if active == previous and state.revisions[active] == incident.healthy_rev:
                try:
                    if state.revisions[target] != release.revision:
                        await self.engine.deploy(target, release.revision, previous)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code not in {422, 503}:
                        raise
                    await self.stop_before_switch(incident, release, previous, "Candidate deployment failed")
                    return
                if not await self.probes_pass(f"/slots/{target}/checkout", target, release.revision, cart):
                    await self.stop_before_switch(incident, release, previous, "Candidate failed pre-switch checkout")
                    return
                await self.engine.switch_to(target, previous)
            elif active != target or state.revisions[active] != release.revision:
                raise RecoveryError("The active slot no longer matches the release plan")

            if not await self.probes_pass("/checkout", target, release.revision, cart):
                reason = "Candidate failed live checkout"
                self.store.require_release_rollback(release.id, reason)
                await self.rollback(incident, release, previous, target, cart, reason)
                return

            await self.engine.promote(target, release.revision)
            self.store.finish_release(incident, release, active_slot=target, active_rev=release.revision)
            self.engine.notify(self.engine.on_released, incident)

    async def stop_before_switch(
        self, incident: IncidentRecord, release: ReleaseRecord, previous: Slot, reason: str
    ) -> None:
        state = await self.engine.snapshot()
        if state.active_slot != previous or state.revisions[previous] != incident.healthy_rev:
            raise RecoveryError("The original route changed before the release could be stopped")
        self.store.finish_release(
            incident, release, active_slot=previous, active_rev=incident.healthy_rev, failure=reason
        )

    async def rollback(
        self,
        incident: IncidentRecord,
        release: ReleaseRecord,
        previous: Slot,
        target: Slot,
        cart: dict[str, Any],
        reason: str,
    ) -> None:
        state = await self.engine.snapshot()
        if state.revisions[previous] != incident.healthy_rev:
            raise RecoveryError("The previous slot no longer runs the restored revision")
        if state.active_slot == target:
            if state.revisions[target] != release.revision:
                raise RecoveryError("The live candidate revision changed before rollback")
            await self.engine.switch_to(previous, target)
        if not await self.engine.checkout_passes("/checkout", previous, incident.healthy_rev, cart):
            raise RecoveryError("Rollback checkout verification is pending")
        if state.healthy_rev != incident.healthy_rev:
            await self.engine.promote(previous, incident.healthy_rev)
        self.store.finish_release(
            incident, release, active_slot=previous, active_rev=incident.healthy_rev, failure=reason
        )

    async def watch(self) -> None:
        while True:
            incident = self.store.current_incident()
            if incident is not None and incident.status == "releasing":
                release = self.store.pending_release(incident.id)
                if release is not None:
                    try:
                        await self.run(incident, release)
                    except (httpx.HTTPError, RecoveryError) as error:
                        logger.warning("Release %s pending: %s", release.id, error)
                        await asyncio.sleep(1)
            await asyncio.sleep(0.25)
