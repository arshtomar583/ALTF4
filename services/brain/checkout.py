"""Independent price expectations for the ShopLite carts PHOENIX monitors."""

import httpx


PLAIN_CART = {"sku": "demo-mug", "quantity": 1}
PROBE_CARTS = (PLAIN_CART, {**PLAIN_CART, "discount": {"code": "SAVE10"}})


def expected_cents(cart: dict) -> int | None:
    quantity = cart.get("quantity")
    if cart.get("sku") != "demo-mug" or type(quantity) is not int or not 1 <= quantity <= 20:
        return None
    if "discount" not in cart:
        return 1200 * quantity
    if cart["discount"] == {"code": "SAVE10"}:
        return 1080 * quantity
    return None


def check_charge(response: httpx.Response | None, cart: dict) -> tuple[bool, int | None]:
    """Return whether checkout passed and the integer charge when available."""
    if response is None or not response.is_success:
        return False, None
    try:
        body = response.json()
    except ValueError:
        return False, None
    if not isinstance(body, dict) or type(total := body.get("totalCents")) is not int:
        return False, None
    return body.get("status") == "accepted" and total == expected_cents(cart), total
