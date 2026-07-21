---
title: Historical Data
area: historical
type: spec
status: locked
tags: [area/historical, type/spec, status/locked]
up: "[[Historical-MOC]]"
related: ["[[bin-structure-spec]]", "[[storage-layout]]", "[[option-chain-selection]]", "[[algo-engine-findings]]"]
---

# Historical Data

Ported from `kite_broker/historical_data/*`. Downloads OHLC + OI candles for a set of
instruments over a date range and stores them in the **same `.bin` format** as live.

## Kite historical API

```
GET https://api.kite.trade/instruments/historical/{instrument_token}/{interval}
    ?from=YYYY-MM-DD HH:MM:SS&to=YYYY-MM-DD HH:MM:SS&oi=1
Headers: X-Kite-Version: 3, Authorization: token {api_key}:{access_token}
```
Response `data.candles` is a list of arrays: `[timestamp, open, high, low, close,
volume, oi?]`.

## Interval policy table

| wire_name | max request/UI days | step (min) | OI |
|---|---|---|---|
| minute   | 60   | 1  | ✅ |
| 3minute  | 100  | 3  | ✅ |
| 5minute  | 100  | 5  | ✅ |
| 10minute | 100  | 10 | ✅ |
| 15minute | 100  | 15 | ✅ |
| 30minute | 365  | 30 | ✅ |
| 60minute | 365  | 60 | ✅ |
| day      | 2000 | 1440 | ✅ |

## Fetch windowing

- Split `[from, to]` into chunks no larger than the interval's `max_request_days` (or
  a user `chunk_size_days`, clamped). Fetch each window sequentially per contract;
  concatenate, sort by timestamp, de-duplicate.

## Rate limiting & retries

- **Token-bucket limiter:** configurable requests/second, burst up to ~8; shared across
  all download tasks (`aiolimiter` or custom).
- **Retries:** up to 5 attempts with exponential backoff on HTTP 429 / 5xx.

## Request validation (preserve these guards)

- `from < to`; requested span ≤ interval `max_ui_days`.
- `weekly_only` and `monthly_only` mutually exclusive.
- ATM-window mode and explicit strike range mutually exclusive.
- expiry string must be `YYYY-MM-DD`.

## Strike / expiry selection

- **Selection modes:** `full_chain` | `atm_window`.
- **Expiry modes:** `specific` | `nearest_weekly` | `nearest_monthly` | `all_expiries`.
- Expired contracts rely on the **archived instrument-master snapshots** (delisted
  tokens vanish from the live dump) — see `_instruments/` in [[storage-layout]].

## Storage — same BIN format as live (decision)

Stored in the same `.bin` format ([[bin-structure-spec]]), not Parquet, so one
reader/toolchain serves both:

```
MARKET_DATA/INDICES_HIS/<INDEX>/<YYYY-MM-DD>.bin[.zst]     # historical index chains
MARKET_DATA/STOCKS_HIS/<YYYY-MM-DD>.bin[.zst]              # historical stock matrix
MARKET_DATA/_state/{job}_{token}.json                      # per-contract checkpoint (resume)
MARKET_DATA/_state/{job}_request.json                      # original request (resume across restart)
```

- Each file: header once (incl. `risk_free_rate`), then one frame per candle timestamp
  — identical schema to live.
- Historical candles provide OHLC + volume + OI only, so bid/ask/depth columns are
  `0`/empty (raw = what the source provides); Greeks/IV remain unstored, reconstructable.

## Frame assembly (following `bin_export.rs`)

- Rows grouped by `(trading_date, timestamp)`; strikes collected into calls/puts columns
  at aligned indices; `sequence` increments per date, preserved across chunked appends.
- Header written once when the file is empty; then one frame per timestamp.
- Same integer-native types and column order as live ([[bin-structure-spec]]).

## Job management

- `spawn` / `cancel` / `resume` / `list` jobs.
- Per-contract checkpoints record `last_completed_timestamp`, completed windows, rows
  written, status — enabling resume after interruption/restart.
- Progress streamed to the frontend over the `historical-jobs` WS topic ([[websocket-protocol]]).

## Concurrency

- Multiple contracts downloaded concurrently with `asyncio.gather` bounded by a
  semaphore, sharing one rate limiter.
- BIN writes offloaded to a thread pool (encoding + I/O + zstd), mirroring the live writer.
