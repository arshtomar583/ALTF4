import express from "express";

export function createShop(revision) {
  const app = express();
  const product = { sku: "demo-mug", unitPriceCents: 1200 };

  app.use(express.json({ limit: "8kb" }));
  app.use((_request, response, next) => {
    response.set("X-Shop-Revision", revision);
    next();
  });

  app.get("/health", (_request, response) => {
    response.json({ status: "ok", revision });
  });

  app.post("/checkout", (request, response) => {
    const { sku, quantity } = request.body ?? {};
    if (sku !== product.sku || !Number.isInteger(quantity) || quantity < 1 || quantity > 20) {
      response.status(400).json({ status: "rejected", message: "Invalid product or quantity" });
      return;
    }

    const code = request.body.discount.code.trim().toUpperCase();
    if (code !== undefined && code !== "SAVE10") {
      response.status(400).json({ status: "rejected", message: "Unknown discount code" });
      return;
    }

    const subtotalCents = product.unitPriceCents * quantity;
    const discountCents = code === undefined ? 0 : Math.floor(subtotalCents / 10);
    response.json({
      status: "accepted",
      sku,
      quantity,
      subtotalCents,
      discountCents,
      totalCents: subtotalCents - discountCents,
      revision,
    });
  });

  return app;
}
