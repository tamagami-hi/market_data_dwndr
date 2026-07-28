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

/** Project one stock row for the table. */
export function stockRow(board: StockBoardPayload, i: number): StockRow {
  const expiries = board.future_expiries[i] ?? [];
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
export function stockRows(board: StockBoardPayload | null): StockRow[] {
  if (!board) return [];
  return Array.from({ length: board.count }, (_, i) => stockRow(board, i));
}

/** The 5 depth levels of one leg for one stock, as level-1..5 objects. */
export function legDepth(leg: LegColumns | undefined, i: number): DepthLevel[] {
  if (!leg) return [];
  return leg.depth.map((level, k) => ({
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
  const expiries = board.future_expiries[i] ?? [];
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
