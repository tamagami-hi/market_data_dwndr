"use client";

/**
 * Projections over the columnar StockBoard payload.
 *
 * The board arrives as one array per field (see `StockBoardPayload`) so the wire stays
 * small. These helpers pull a single stock back out by row index — nothing is copied
 * until a row is actually rendered.
 *
 * Depth now arrives on the live stream, so `depthFromBoard` replaces the old on-demand
 * `GET /api/capture/stocks/{symbol}/depth` fetch: expanding a row is instant and the
 * order book keeps updating every second instead of freezing until reopened.
 */

import type {
  DepthLevel,
  LegColumns,
  StockBoardPayload,
  StockDepthSnapshot,
  StockLegName,
  StockRow,
} from "@/lib/wsTypes";

const FUTURE_LEGS: StockLegName[] = ["fut_current", "fut_mid", "fut_far"];
const FUTURE_LABELS = ["Current future", "Mid future", "Far future"];
const STOCK_LEGS: StockLegName[] = ["spot", ...FUTURE_LEGS];
const MAX_STOCK_ROWS = 2_000;
const DEPTH_LEVELS = 5;
const FUTURE_LEG_COUNT = 3;
const SCALAR_KEYS = [
  "ltp",
  "oi",
  "volume",
  "buy_quantity",
  "sell_quantity",
  "oi_day_high",
  "oi_day_low",
  "ohlc_open",
  "ohlc_high",
  "ohlc_low",
  "ohlc_close",
] as const;

function isNumberArray(value: unknown, count: number): value is number[] {
  return Array.isArray(value) &&
    value.length === count &&
    value.every((item) => typeof item === "number" && Number.isFinite(item));
}

export function normalizeStockBoard(value: unknown): StockBoardPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const board = value as Record<string, unknown>;
  const count = typeof board.count === "number" &&
    Number.isInteger(board.count) &&
    board.count >= 0 &&
    board.count <= MAX_STOCK_ROWS
    ? board.count
    : -1;
  if (
    count < 0 ||
    typeof board.timestamp !== "number" ||
    !Number.isFinite(board.timestamp) ||
    !Array.isArray(board.tradingsymbols) ||
    !Array.isArray(board.names) ||
    !Array.isArray(board.future_expiries) ||
    board.tradingsymbols.length !== count ||
    board.names.length !== count ||
    board.future_expiries.length !== count ||
    !isNumberArray(board.live_spread, count) ||
    !isNumberArray(board.daily_spread, count) ||
    !board.tradingsymbols.slice(0, count).every((item) => typeof item === "string") ||
    !board.names.slice(0, count).every((item) => typeof item === "string") ||
    !board.future_expiries.every(
      (expiries) =>
        Array.isArray(expiries) &&
        expiries.length <= FUTURE_LEG_COUNT &&
        expiries.every((expiry) => typeof expiry === "string"),
    )
  ) {
    return null;
  }
  const legs = board.legs;
  if (!legs || typeof legs !== "object" || Array.isArray(legs)) return null;
  const validLegs = STOCK_LEGS.every((legName) => {
    const leg = (legs as Record<string, unknown>)[legName];
    if (!leg || typeof leg !== "object" || Array.isArray(leg)) return false;
    const { scalars, depth } = leg as { scalars?: unknown; depth?: unknown };
    if (!scalars || typeof scalars !== "object" || Array.isArray(scalars)) return false;
    if (!SCALAR_KEYS.every((key) => isNumberArray((scalars as Record<string, unknown>)[key], count))) return false;
    return Array.isArray(depth) && depth.length === DEPTH_LEVELS && depth.every((level) => {
      if (!level || typeof level !== "object" || Array.isArray(level)) return false;
      return ["bid_price", "bid_qty", "bid_orders", "ask_price", "ask_qty", "ask_orders"].every(
        (key) => isNumberArray((level as Record<string, unknown>)[key], count),
      );
    });
  });
  return validLegs ? value as StockBoardPayload : null;
}

/** Project one stock row for the table. */
export function stockRow(board: StockBoardPayload, i: number): StockRow {
  const expiries = (board.future_expiries[i] ?? []).slice(0, FUTURE_LEG_COUNT);
  return {
    row: i,
    tradingsymbol: board.tradingsymbols[i],
    name: board.names[i],
    spot_ltp: board.legs.spot.scalars.ltp[i] ?? 0,
    futures: expiries.map((expiry, k) => {
      const leg = board.legs[FUTURE_LEGS[k]];
      return {
        expiry,
        ltp: leg?.scalars.ltp[i] ?? 0,
        oi: leg?.scalars.oi[i] ?? 0,
      };
    }),
    live_spread: board.live_spread[i] ?? 0,
    daily_spread: board.daily_spread[i] ?? 0,
  };
}

/** All rows, in board order. */
export function stockRows(board: StockBoardPayload | null, previous: StockRow[] = []): StockRow[] {
  if (!board) return [];
  return Array.from({ length: board.count }, (_, i) => {
    const next = stockRow(board, i);
    const prior = previous[i];
    return prior && areStockRowsEqual(prior, next) ? prior : next;
  });
}

export function areStockRowsEqual(left: StockRow, right: StockRow): boolean {
  return (
    left.tradingsymbol === right.tradingsymbol &&
    left.name === right.name &&
    left.spot_ltp === right.spot_ltp &&
    left.live_spread === right.live_spread &&
    left.daily_spread === right.daily_spread &&
    left.futures.length === right.futures.length &&
    left.futures.every((future, index) => {
      const other = right.futures[index];
      return other && future.expiry === other.expiry && future.ltp === other.ltp && future.oi === other.oi;
    })
  );
}

/** The 5 depth levels of one leg for one stock, as level-1..5 objects. */
export function legDepth(leg: LegColumns | undefined, i: number): DepthLevel[] {
  if (!leg) return [];
  return leg.depth.slice(0, DEPTH_LEVELS).map((level, k) => ({
    level: k + 1,
    bid_price: level.bid_price[i] ?? 0,
    bid_qty: level.bid_qty[i] ?? 0,
    bid_orders: level.bid_orders[i] ?? 0,
    ask_price: level.ask_price[i] ?? 0,
    ask_qty: level.ask_qty[i] ?? 0,
    ask_orders: level.ask_orders[i] ?? 0,
  }));
}

/**
 * Build the same shape the REST depth endpoint used to return, straight from the live
 * board — so the depth panel renders live data with no fetch.
 */
export function depthFromBoard(
  board: StockBoardPayload | null,
  i: number,
): StockDepthSnapshot | null {
  if (!board || i < 0 || i >= board.count) return null;
  const expiries = (board.future_expiries[i] ?? []).slice(0, FUTURE_LEG_COUNT);
  return {
    tradingsymbol: board.tradingsymbols[i],
    name: board.names[i],
    spot_depth: legDepth(board.legs.spot, i),
    futures: expiries.map((expiry, k) => ({
      label: FUTURE_LABELS[k] ?? `Future ${k + 1}`,
      expiry,
      depth: legDepth(board.legs[FUTURE_LEGS[k]], i),
    })),
  };
}

/** Every captured scalar for a stock's leg — used by the expanded detail view. */
export function legScalars(
  board: StockBoardPayload | null,
  leg: StockLegName,
  i: number,
): Record<string, number> {
  if (!board) return {};
  const scalars = board.legs[leg]?.scalars;
  if (!scalars) return {};
  return Object.fromEntries(
    Object.entries(scalars).map(([field, column]) => [field, column[i] ?? 0]),
  );
}
