import assert from "node:assert/strict";
import { test } from "node:test";
import request from "supertest";

import { createShop } from "../src/shop.js";

test("a cart without a discount checks out at the listed price", async () => {
  const response = await request(createShop("baseline"))
    .post("/checkout")
    .send({ sku: "demo-mug", quantity: 2 })
    .expect("X-Shop-Revision", "baseline")
    .expect(200);

  assert.equal(response.body.totalCents, 2400);
});
test("a valid discount is subtracted from the total", async () => {
  const response = await request(createShop("baseline"))
    .post("/checkout")
    .send({ sku: "demo-mug", quantity: 2, discount: { code: "SAVE10", amount: 120 } })
    .expect(200);

  assert.equal(response.body.totalCents, 2280);
});

test("a valid discount works on a single item", async () => {
  const response = await request(createShop("baseline"))
    .post("/checkout")
    .send({ sku: "demo-mug", quantity: 1, discount: { code: "SAVE10", amount: 120 } })
    .expect(200);

  assert.equal(response.body.totalCents, 1080);
});

test("an unknown discount code is rejected", async () => {
  await request(createShop("baseline"))
    .post("/checkout")
    .send({ sku: "demo-mug", quantity: 2, discount: { code: "FAKE", amount: 120 } })
    .expect(400);
});