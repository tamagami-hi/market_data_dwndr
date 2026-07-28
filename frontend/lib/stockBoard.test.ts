import { describe, expect, test } from "vitest";

import {
  areStockRowsEqual,
  depthFromBoard,
  legScalars,
  normalizeStockBoard,
  stockRows,
} from "@/lib/stockBoard";
import type { StockBoardPayload } from "@/lib/wsTypes";

const scalar = (value: number) => ({
  ltp: [value],
  oi: [10],
  volume: [20],
  buy_quantity: [30],
  sell_quantity: [40],
  oi_day_high: [50],
  oi_day_low: [5],
  ohlc_open: [value],
  ohlc_high: [value],
  ohlc_low: [value],
  ohlc_close: [value],
});
const depth = (value: number) =>
  Array.from({ length: 5 }, () => ({
    bid_price: [value - 1],
    bid_qty: [10],
    bid_orders: [1],
    ask_price: [value + 1],
    ask_qty: [20],
    ask_orders: [2],
  }));
const board = (spot = 100): StockBoardPayload => ({
  timestamp: 1,
  count: 1,
  tradingsymbols: ["AAA"],
  names: ["AAA"],
  future_expiries: [["2026-07-30", "2026-08-27", "2026-09-24"]],
  legs: {
    spot: { scalars: scalar(spot), depth: depth(spot) },
    fut_current: { scalars: scalar(101), depth: depth(101) },
    fut_mid: { scalars: scalar(102), depth: depth(102) },
    fut_far: { scalars: scalar(103), depth: depth(103) },
  },
  live_spread: [1],
  daily_spread: [2],
});

describe("stock board adapter and projections", () => {
  test("rejects malformed columnar payloads and accepts full L1-L5 boards", () => {
    expect(normalizeStockBoard(board())).not.toBeNull();
    expect(normalizeStockBoard({ count: 1, names: [] })).toBeNull();
    expect(normalizeStockBoard({ ...board(), legs: {} })).toBeNull();
    expect(normalizeStockBoard({ ...board(), future_expiries: ["2026-07-30"] })).toBeNull();
    expect(
      normalizeStockBoard({
        ...board(),
        legs: {
          ...board().legs,
          spot: {
            ...board().legs.spot,
            depth: [...board().legs.spot.depth, { bid_price: "invalid" }],
          },
        },
      }),
    ).toBeNull();
    expect(normalizeStockBoard({ ...board(), count: 100_000 })).toBeNull();
  });

  test("reuses unchanged row models and replaces changed rows", () => {
    const previous = stockRows(board());
    const same = stockRows(board(), previous);
    expect(same[0]).toBe(previous[0]);
    const changed = stockRows(board(105), previous);
    expect(changed[0]).not.toBe(previous[0]);
    expect(areStockRowsEqual(changed[0], previous[0])).toBe(false);
  });

  test("projects every scalar and five depth levels without mutation", () => {
    const payload = board();
    const before = structuredClone(payload);
    const snapshot = depthFromBoard(payload, 0);
    expect(snapshot?.spot_depth).toHaveLength(5);
    expect(snapshot?.futures).toHaveLength(3);
    expect(legScalars(payload, "spot", 0)).toMatchObject({ ltp: 100, volume: 20 });
    expect(depthFromBoard(payload, -1)).toBeNull();
    expect(legScalars(null, "spot", 0)).toEqual({});
    expect(payload).toEqual(before);
  });
});
