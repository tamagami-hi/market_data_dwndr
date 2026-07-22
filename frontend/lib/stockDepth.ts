import type { DepthLevel, StockDepthSnapshot } from "@/lib/wsTypes";

function asObject(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Stock depth response is invalid.");
  }
  return value as Record<string, unknown>;
}

function finiteNumber(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("Stock depth levels must contain finite numbers.");
  }
  return value;
}

function normalizeDepth(value: unknown): DepthLevel[] {
  if (!Array.isArray(value) || value.length !== 5) {
    throw new Error("Stock depth must contain exactly five valid levels.");
  }
  return value.map((item) => {
    const level = asObject(item);
    return {
      level: finiteNumber(level.level),
      bid_price: finiteNumber(level.bid_price),
      bid_qty: finiteNumber(level.bid_qty),
      bid_orders: finiteNumber(level.bid_orders),
      ask_price: finiteNumber(level.ask_price),
      ask_qty: finiteNumber(level.ask_qty),
      ask_orders: finiteNumber(level.ask_orders),
    };
  });
}

export function normalizeStockDepth(value: unknown): StockDepthSnapshot {
  const payload = asObject(value);
  if (typeof payload.tradingsymbol !== "string" || typeof payload.name !== "string") {
    throw new Error("Stock depth response has no symbol.");
  }
  if (!Array.isArray(payload.futures)) {
    throw new Error("Stock depth response has no futures array.");
  }
  return {
    tradingsymbol: payload.tradingsymbol,
    name: payload.name,
    spot_depth: normalizeDepth(payload.spot_depth),
    futures: payload.futures.slice(0, 3).map((item) => {
      const future = asObject(item);
      if (typeof future.label !== "string" || typeof future.expiry !== "string") {
        throw new Error("Stock future depth response is invalid.");
      }
      return {
        label: future.label,
        expiry: future.expiry,
        depth: normalizeDepth(future.depth),
      };
    }),
  };
}
