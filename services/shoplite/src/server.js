import { createShop } from "./shop.js";

const port = Number(process.env.PORT ?? 3001);
const revision = process.env.SHOP_REVISION ?? "starter";

createShop(revision).listen(port, "0.0.0.0", () => {
  console.log("ShopLite listening on port", port, "at revision", revision);
});
