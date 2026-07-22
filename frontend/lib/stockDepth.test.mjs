import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import ts from "typescript";

const source = await readFile(new URL("./stockDepth.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { normalizeStockDepth } = await import(moduleUrl);

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

  assert.equal(result.spot_depth.length, 5);
  assert.equal(result.futures[0].depth[4].level, 5);
});

test("rejects missing or non-finite depth without crashing the view", () => {
  assert.throws(
    () => normalizeStockDepth({ tradingsymbol: "RELIANCE", name: "RELIANCE", spot_depth: [], futures: [] }),
    /five valid levels/,
  );
  const invalid = validDepth();
  invalid[2] = { ...invalid[2], ask_price: Number.NaN };
  assert.throws(
    () => normalizeStockDepth({ tradingsymbol: "RELIANCE", name: "RELIANCE", spot_depth: invalid, futures: [] }),
    /finite numbers/,
  );
});
