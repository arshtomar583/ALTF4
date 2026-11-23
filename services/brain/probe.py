"""Measure a real request through the active checkout route."""

import asyncio
import time
from itertools import cycle

import httpx

from .event_store import CheckoutProbe, EventStore


PLAIN_CART = {"sku": "demo-mug", "quantity": 1}
# Each cart with the total a healthy checkout charges for it.
CARTS = (
    (PLAIN_CART, 1200),
    ({**PLAIN_CART, "discount": {"code": "SAVE10"}}, 1080),
)


def charged_cents(response: httpx.Response | None, expected: int) -> int:
    """The total checkout charged, or the total it should have charged when it failed."""
    if response is None or not response.is_success:
        return expected
    try:
        total = response.json()["totalCents"]
    except (ValueError, KeyError, TypeError):
        return expected
    return total if isinstance(total, int) else expected


async def poll_checkout(store: EventStore, checkout_url: str, interval: float) -> None:
    async with httpx.AsyncClient(timeout=2.0) as client:
        for cart, expected in cycle(CARTS):
            started = time.perf_counter()
            try:
                response = await client.post(checkout_url, json=cart)
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
                    discount="discount" in cart,
                    cart_total_cents=charged_cents(response, expected),
                    request_body=cart,
                )
            )
            await asyncio.sleep(max(0, interval - elapsed))
