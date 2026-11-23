"""Measure a real request through the active checkout route."""

import asyncio
import time

import httpx

from .event_store import CheckoutProbe, EventStore


async def poll_checkout(store: EventStore, checkout_url: str, interval: float) -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            started = time.perf_counter()
            try:
                response = await client.post(checkout_url, json={"sku": "demo-mug", "quantity": 1})
            except httpx.RequestError:
                response = None

            elapsed = time.perf_counter() - started
            store.record_probe(
                CheckoutProbe(
                    ok=response is not None and response.is_success,
                    status_code=response.status_code if response is not None else None,
                    latency_ms=round(elapsed * 1000),
                    slot=response.headers.get("x-phoenix-slot") if response is not None else None,
                    revision=response.headers.get("x-shop-revision") if response is not None else None,
                )
            )
            await asyncio.sleep(max(0, interval - elapsed))
