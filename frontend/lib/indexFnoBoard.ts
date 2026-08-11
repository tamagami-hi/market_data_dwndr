"use client";

/**
 * Projections over the columnar IndexFnoBoard payload (index spot + index futures).
 *
 * The wire format is deliberately identical to `StockBoardPayload` apart from its identity
 * arrays: an index row is named by `underlyings[i]` / `spot_symbols[i]` because the on-disk
 * index record carries no tradingsymbol. So rather than duplicate the row projection, the
 * depth rebuild and the scalar extraction, this module validates the index-specific fields
 * and then adapts the board into the stock shape — after which every existing helper and
 * every existing row renderer works on it unchanged. That adaptation is what guarantees
 * the two groups render in the same format instead of drifting apart.
 */

import {
  hasValidExpiries,
  hasValidLegs,
  isNumberArray,
} from "@/lib/stockBoard";
import type { IndexFnoBoardPayload, StockBoardPayload } from "@/lib/wsTypes";

/** Six configured indices today; the cap only exists to bound a malformed payload. */
const MAX_INDEX_ROWS = 64;

export function normalizeIndexFnoBoard(value: unknown): IndexFnoBoardPayload | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const board = value as Record<string, unknown>;
  const count = typeof board.count === "number" &&
    Number.isInteger(board.count) &&
    board.count >= 0 &&
    board.count <= MAX_INDEX_ROWS
    ? board.count
    : -1;
  if (
    count < 0 ||
    typeof board.timestamp !== "number" ||
    !Number.isFinite(board.timestamp) ||
    !Array.isArray(board.underlyings) ||
    !Array.isArray(board.spot_symbols) ||
    board.underlyings.length !== count ||
    board.spot_symbols.length !== count ||
    !board.underlyings.every((item) => typeof item === "string") ||
    !board.spot_symbols.every((item) => typeof item === "string") ||
    !hasValidExpiries(board.future_expiries, count) ||
    !isNumberArray(board.live_spread, count) ||
    !isNumberArray(board.daily_spread, count)
  ) {
    return null;
  }
  return hasValidLegs(board.legs, count) ? (value as IndexFnoBoardPayload) : null;
}

/**
 * Present the index board as a stock board so the shared projections apply.
 *
 * `tradingsymbol` becomes the underlying (NIFTY, BANKNIFTY, …) and `name` the spot symbol
 * (`NSE:NIFTY 50`). The leg arrays are passed by reference — this is a thin relabelling,
 * not a copy of the per-second market data.
 */
export function indexFnoAsStockBoard(board: IndexFnoBoardPayload): StockBoardPayload {
  return {
    timestamp: board.timestamp,
    count: board.count,
    tradingsymbols: board.underlyings,
    names: board.spot_symbols,
    future_expiries: board.future_expiries,
    legs: board.legs,
    live_spread: board.live_spread,
    daily_spread: board.daily_spread,
  };
}
