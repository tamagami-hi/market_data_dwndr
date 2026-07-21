---
title: BIN Format & Storage
area: data-storage
type: spec
status: locked
tags: [area/data-storage, type/spec, status/locked]
up: "[[Data-Storage-MOC]]"
related: ["[[bin-structure-spec]]", "[[storage-layout]]", "[[lossless-and-precision]]", "[[stocks-capture]]"]
---

# BIN Format & Storage

**Decision (locked):** we use `algo_engine`'s **storage approach** — little-endian
fixed-width encoding, `[u32 LE payload_len][payload]` framing, one header frame first
then data frames appended **frame-by-frame**, whole file zstd L17 at end of day — but
with **our own lean, integer-native schema**. No raw information is lost; everything
computable is left out and recreated on read.

> The exact byte-level layout lives in [[bin-structure-spec]]. This note is the
> rationale/overview.

## Why it's our own schema (not byte-identical to algo_engine)

1. We store the **10-yr bond yield** in the header so Greeks are reconstructable.
2. We **do not store Greeks/IV at all** (not even `0.0`).
3. We store **native integers** (paise for prices, counts for qty/OI) rather than `f64`.

Consequently these files are **not readable by the unmodified `algo_engine` reader**.
We ship a small dedicated reader (Python; optional Rust variant later). This is the
deliberate trade for lossless integers + stored bond yield + zero redundant columns.

## Data types (lossless, integer-native)

| Kind | Stored as | Note |
|---|---|---|
| prices: ltp, bid, ask, OHLC, strike, spot, vix | `i64` | exchange **paise** (`value × 100`); divide on read |
| quantities: volume, buy/sell qty, bid/ask qty | `u64` | native counts |
| order counts (L5 depth) | `u32` | per level |
| open interest: oi, oi_day_high/low | `u64` | native counts |
| timestamp, sequence, tokens | `u64` | — |
| risk_free_rate (bond yield) | `f64` | the only float; single scalar in the header |

Bit-exact to the wire (no float rounding). See [[lossless-and-precision]].

## Depth levels (locked)

- **Indices (option chain): L1** — best bid/ask only, matching `algo_engine` and the
  professional norm for full-chain capture ([[depth-level-research]]). 15 raw columns/side.
- **Stocks: L5** — full top-5 order book each side for spot + each future, where depth
  helps the calendar-spread/arbitrage use case ([[stocks-capture]]).

## Frame model (both file types)

- `[u32 LE payload_len][payload]` per frame; header frame (tag `0`) once when the file
  is empty, then data frames (tag `1`) appended once per second (1 Hz, [[live-capture-performance]]).
- **Index file:** `IndexHeader` + `IndexFrame`s; `strikes` (paise) live once in the
  header (fixed ATM ± 50 window for the day) — lossless and avoids repetition.
- **Stocks file:** `StockHeader` (board + bond yield) + `StockFrame`s (a matrix of raw
  columns across all stocks). One file/day for all stocks ([[storage-layout]], [[stocks-capture]]).

## Dropped (computable, reconstructed on read)

`iv, delta, gamma, vega, theta, rho` (Black-Scholes from stored raw + header
`risk_free_rate`), `change` (`ltp − ohlc_close`), `change_in_oi`
(`oi − prev-day-close OI`), and CalSpread spreads/summary stats. All reproducible.

## Compression

Whole-file **zstd level 17** at end of day → `<date>.bin.zst`, raw removed. Lossless;
level affects only ratio, not fidelity.

## Reader

Dedicated reader: (decompress if `.zst`) → scan once building `timestamp → (offset,
size)` index → binary search / random access. Divides paise→rupees and computes Greeks
lazily from the stored bond yield. Full byte layout: [[bin-structure-spec]].
