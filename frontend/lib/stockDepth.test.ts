import { expect, test } from "vitest";

import { normalizeStockDepth } from "@/lib/stockDepth";

function validDepth() {
  return Array.from({ length: 5 }, (_, index) => ({
    level: index + 1,
    bid_price: 100 + index,
    bid_qty: 10 + index,
    bid_orders: 1 + index,
    ask_price: 101 + index,
    ask_qty: 20 + index,
    ask_orders: 2 + index,
  }));
}

test("accepts exactly five finite stock depth levels", () => {
  const result = normalizeStockDepth({
    tradingsymbol: "RELIANCE",
    name: "RELIANCE",
    spot_depth: validDepth(),
    futures: [{ label: "Current future", expiry: "2026-07-30", depth: validDepth() }],
  });

  expect(result.spot_depth).toHaveLength(5);
  expect(result.futures[0].depth[4].level).toBe(5);
});

test("rejects missing or non-finite depth without crashing the view", () => {
  expect(() =>
    normalizeStockDepth({ tradingsymbol: "RELIANCE", name: "RELIANCE", spot_depth: [], futures: [] }),
  ).toThrow(/five valid levels/);
  const invalid = validDepth();
  invalid[2] = { ...invalid[2], ask_price: Number.NaN };
  expect(() =>
    normalizeStockDepth({ tradingsymbol: "RELIANCE", name: "RELIANCE", spot_depth: invalid, futures: [] }),
  ).toThrow(/finite numbers/);
});
