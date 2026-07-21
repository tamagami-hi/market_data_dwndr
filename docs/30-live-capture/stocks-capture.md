---
title: Stocks Capture (Calendar-Spread Board)
area: live-capture
type: spec
status: locked
tags: [area/live-capture, type/spec, status/locked]
up: "[[Live-Capture-MOC]]"
related: ["[[bin-structure-spec]]", "[[live-data-pipeline]]", "[[depth-level-research]]", "[[lossless-and-precision]]"]
source: CalSpread (Cal_Spread_Backend)
---

# Stocks Capture (Calendar-Spread Board, Matrix Format)

Reference: `Cal_Spread_Backend` (CalSpread). We reuse its **board discovery** and
**price sources**, store the board **as a matrix** (rows = stocks, columns = raw
fields), frame-by-frame at 1 Hz — and **do not store any computable metric** (spread,
mean, std-dev, percentile, mean-reversion, etc.). No info loss: everything CalSpread
computes is derivable from the raw columns we keep.

## Storage target

One file per day, all stocks: `MARKET_DATA/STOCKS/<YYYY-MM-DD>.bin[.zst]` (historical
under `STOCKS_HIS/`). See [[storage-layout]].

## Board discovery (ported from CalSpread `deriveFnoBoard` / `deriveFnoStocks`)

From the Kite instrument dump (public CSV, no auth needed):

1. Every `NFO` `FUT` row with a `name` → that `name` is an **underlying**; collect its
   futures.
2. Match to an `NSE` `EQ` row by `tradingsymbol` → the **spot** (+ `lot_size`).
   Underlyings with no EQ row are indices → excluded here (handled by the index pipeline).
3. Sort each underlying's futures by expiry, keep the **3 nearest**: `[current, mid, far]`.

Re-derived **daily** so expiry roll-over is automatic. The resolved board is written
into the file header once.

## Subscription

Subscribe **spot + up to 3 futures** per stock, `full` mode, on the shared WebSocket
(full universe ≈ 200 stocks × 4 ≈ 800 tokens; [[live-capture-performance]]). Route
ticks by token into the in-memory matrix; snapshot the whole matrix once per second.

## The matrix

- **Header (once)** carries the static board: per stock the `NAME` and its
  spot/futures references (the non-computable identifiers CalSpread uses).
- **Each 1-Hz frame** is the matrix of raw values: current price (spot), current/mid/
  far-month futures LTP, plus raw OHLC / OI / volume / **L5 depth** per contract.
  Columns are arrays across all stocks (row order fixed by the header).
- **Depth: L5 (locked)** — top-5 order book each side for spot and each future. This
  is where depth helps the calendar-spread / arbitrage use case; cheap at 1 Hz.
  CalSpread used only LTP, so L5 is strictly more information ([[depth-level-research]]).

## Schema

The exact byte-level layout is in [[bin-structure-spec]] (§4). In brief: `StockHeader`
(board + `risk_free_rate`) written once, then one `StockFrame` per second. Each frame
holds four `InstrColumns` matrices — `spot`, `fut_current`, `fut_mid`, `fut_far` —
each a set of columnar arrays across all N stocks, with L5 depth (5 levels × bid/ask
price/qty/orders).

- **Integer-native types** ([[lossless-and-precision]]): prices `i64` paise,
  quantities/OI `u64`, orders `u32`; `risk_free_rate` is the only `f64`.
- **Missing futures:** a stock with < 3 futures has undefined values in the unused
  `fut_*` slot; validity comes from `StockRef.futures.len()`. Price sentinel = `0`.

## Computable metrics we deliberately DO NOT store (reconstructable)

- **Live/hourly spread** = `fut_mid.ltp − fut_current.ltp`.
- **Daily spread** = `fut_mid.close − fut_current.close`.
- **Summary stats** (mean, min/max, mean-deviation, std-dev, 95th percentile,
  mean-reversion probability) — computed offline from stored closes.

A separate offline module can rebuild CalSpread's `hourlyprices`, `spread_daily`, and
`spread_summary` from these files on demand; capture stays strictly raw.
