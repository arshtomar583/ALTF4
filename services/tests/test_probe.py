import asyncio
import json

import httpx
import pytest

from brain import probe
from brain.event_store import EventStore

pytestmark = pytest.mark.asyncio
PLAIN = {"sku": "demo-mug", "quantity": 1}
SAVE10 = {**PLAIN, "discount": {"code": "SAVE10"}}


async def record(monkeypatch, tmp_path, answer, count):
    """Run the real probe loop against a fake shop until it has recorded count probes."""
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return answer(sent[-1])

    original = httpx.AsyncClient
    monkeypatch.setattr(
        probe.httpx, "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    store = EventStore(tmp_path / "probe.sqlite3")
    store.initialize()
    task = asyncio.create_task(probe.poll_checkout(store, "http://shop/checkout", 0.01))
    while store.latest_id() < count:
        await asyncio.sleep(0.01)
    task.cancel()
    return sent, [event["data"] for event in store.after(0)][:count]


async def test_probe_alternates_carts_and_records_what_it_sent(monkeypatch, tmp_path):
    def answer(body):
        return httpx.Response(200, json={"totalCents": 1080 if "discount" in body else 1200})

    sent, probes = await record(monkeypatch, tmp_path, answer, 4)
    assert sent[:4] == [PLAIN, SAVE10, PLAIN, SAVE10]
    assert [item["discount"] for item in probes] == [False, True, False, True]
    assert [item["request_body"] for item in probes] == sent[:4]
    assert [item["cart_total_cents"] for item in probes] == [1200, 1080, 1200, 1080]
    assert all(item["ok"] for item in probes)


async def test_failed_probe_records_the_total_it_should_have_charged(monkeypatch, tmp_path):
    def answer(body):
        return httpx.Response(200, json={"totalCents": 1}) if "discount" in body else httpx.Response(500)

    _, probes = await record(monkeypatch, tmp_path, answer, 2)
    assert [item["ok"] for item in probes] == [False, True]
    # A wrong charge is recorded as charged, never replaced by the expected total.
    assert [item["cart_total_cents"] for item in probes] == [1200, 1]


@pytest.mark.parametrize("reply", [httpx.Response(200, text="oops"), httpx.Response(200, json=[]), None])
async def test_unreadable_answers_do_not_stop_the_probe(reply):
    assert probe.charged_cents(reply, 1200) == 1200
