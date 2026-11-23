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

function checkout(body) {
  return request(createShop("baseline")).post("/checkout").send({ sku: "demo-mug", quantity: 2, ...body });
}

test("a cart without a discount reports its subtotal", async () => {
  const response = await checkout({}).expect(200);
  assert.equal(response.body.subtotalCents, 2400);
  assert.equal(response.body.discountCents, 0);
  assert.equal(response.body.totalCents, 2400);
});

test("SAVE10 takes ten percent off", async () => {
  const response = await checkout({ discount: { code: "SAVE10" } }).expect(200);
  assert.equal(response.body.subtotalCents, 2400);
  assert.equal(response.body.discountCents, 240);
  assert.equal(response.body.totalCents, 2160);
});

for (const code of ["FREE", "save10", "", null, ["SAVE10"]]) {
  test(`the discount code ${JSON.stringify(code)} is rejected`, async () => {
    const response = await checkout({ discount: { code } }).expect(400);
    assert.deepEqual(response.body, { status: "rejected", message: "Unknown discount code" });
  });
}
