"""Measure a real request through the active checkout route."""

import asyncio
import time
from itertools import cycle

import httpx

from .checkout import PROBE_CARTS, check_charge, expected_cents
from .event_store import CheckoutProbe, EventStore


async def poll_checkout(store: EventStore, checkout_url: str, interval: float) -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        for cart in cycle(PROBE_CARTS):
            started = time.perf_counter()
            try:
                response = await client.post(checkout_url, json=cart)
            except httpx.RequestError:
                response = None

            elapsed = time.perf_counter() - started
            ok, charged = check_charge(response, cart)
            store.record_probe(
                CheckoutProbe(
                    ok=ok,
                    status_code=response.status_code if response is not None else None,
                    latency_ms=round(elapsed * 1000),
                    slot=response.headers.get("x-phoenix-slot") if response is not None else None,
                    revision=response.headers.get("x-shop-revision") if response is not None else None,
                    discount="discount" in cart,
                    cart_total_cents=charged if charged is not None else expected_cents(cart),
                    request_body=cart,
                )
            )
            await asyncio.sleep(max(0, interval - elapsed))
