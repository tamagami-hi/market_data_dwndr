import { describe, expect, test } from "vitest";

import { indexFnoAsStockBoard, normalizeIndexFnoBoard } from "@/lib/indexFnoBoard";
import { stockRows } from "@/lib/stockBoard";
import type { IndexFnoBoardPayload } from "@/lib/wsTypes";

function leg(value: number, count: number) {
  const column = (v: number) => Array.from({ length: count }, () => v);
  return {
    scalars: {
      ltp: column(value),
      oi: column(10),
      volume: column(20),
      buy_quantity: column(30),
      sell_quantity: column(40),
      oi_day_high: column(50),
      oi_day_low: column(5),
      ohlc_open: column(value),
      ohlc_high: column(value + 1),
      ohlc_low: column(value - 1),
      ohlc_close: column(value),
    },
    depth: Array.from({ length: 5 }, () => ({
      bid_price: column(value - 1),
      bid_qty: column(10),
      bid_orders: column(1),
      ask_price: column(value + 1),
      ask_qty: column(20),
      ask_orders: column(2),
    })),
  };
}

function board(overrides: Partial<IndexFnoBoardPayload> = {}): IndexFnoBoardPayload {
  return {
    timestamp: 1_700_000_000_000,
    count: 2,
    underlyings: ["NIFTY", "BANKNIFTY"],
    spot_symbols: ["NSE:NIFTY 50", "NSE:NIFTY BANK"],
    future_expiries: [
      ["2026-08-25", "2026-09-29"],
      ["2026-08-25", "2026-09-29", "2026-10-27"],
    ],
    legs: {
      spot: leg(24_540, 2),
      fut_current: leg(24_600, 2),
      fut_mid: leg(24_700, 2),
      fut_far: leg(24_800, 2),
    },
    live_spread: [100, 120],
    daily_spread: [-5, 8],
    ...overrides,
  };
}

test("accepts a well-formed index board", () => {
  expect(normalizeIndexFnoBoard(board())).not.toBeNull();
});

describe("rejects malformed payloads rather than rendering partial rows", () => {
  test.each([
    ["identity array shorter than count", { underlyings: ["NIFTY"] }],
    ["spot symbols shorter than count", { spot_symbols: ["NSE:NIFTY 50"] }],
    ["a non-string underlying", { underlyings: ["NIFTY", 7] as unknown as string[] }],
    ["more expiries than future legs", {
      future_expiries: [["a", "b", "c", "d"], ["a"]] as string[][],
    }],
    ["a spread array of the wrong length", { live_spread: [1] }],
    ["a missing leg", {
      legs: { spot: leg(1, 2) } as unknown as IndexFnoBoardPayload["legs"],
    }],
    ["a non-integer count", { count: 1.5 }],
  ])("%s", (_label, overrides) => {
    expect(normalizeIndexFnoBoard(board(overrides as Partial<IndexFnoBoardPayload>))).toBeNull();
  });

  test("a non-object payload", () => {
    expect(normalizeIndexFnoBoard(null)).toBeNull();
    expect(normalizeIndexFnoBoard([])).toBeNull();
  });
});

test("an empty board is valid — capture may run with index F&O disabled", () => {
  const empty = normalizeIndexFnoBoard({
    timestamp: 1,
    count: 0,
    underlyings: [],
    spot_symbols: [],
    future_expiries: [],
    legs: {
      spot: leg(0, 0),
      fut_current: leg(0, 0),
      fut_mid: leg(0, 0),
      fut_far: leg(0, 0),
    },
    live_spread: [],
    daily_spread: [],
  });
  expect(empty).not.toBeNull();
  expect(stockRows(indexFnoAsStockBoard(empty!))).toEqual([]);
});

test("relabels the index identity onto the stock row shape so renderers are shared", () => {
  const rows = stockRows(indexFnoAsStockBoard(board()));
  expect(rows).toHaveLength(2);
  // The underlying is the row's identity; the spot symbol is its secondary label.
  expect(rows[0].tradingsymbol).toBe("NIFTY");
  expect(rows[0].name).toBe("NSE:NIFTY 50");
  expect(rows[0].spot_ltp).toBe(24_540);
  expect(rows[0].live_spread).toBe(100);
  expect(rows[0].daily_spread).toBe(-5);
  // Board order is preserved (configured capture order), not sorted.
  expect(rows.map((row) => row.tradingsymbol)).toEqual(["NIFTY", "BANKNIFTY"]);
  // Only the expiries actually present become future cells.
  expect(rows[0].futures).toHaveLength(2);
  expect(rows[1].futures).toHaveLength(3);
  expect(rows[0].futures[0]).toEqual({ expiry: "2026-08-25", ltp: 24_600, oi: 10 });
});

test("the adapter shares the leg arrays instead of copying market data each tick", () => {
  const source = board();
  const adapted = indexFnoAsStockBoard(source);
  expect(adapted.legs).toBe(source.legs);
  expect(adapted.tradingsymbols).toBe(source.underlyings);
});
