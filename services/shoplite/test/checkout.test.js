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
