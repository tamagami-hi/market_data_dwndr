---
title: Overview & Scope
area: overview
type: overview
status: locked
tags: [area/overview, type/overview, status/locked]
up: "[[Overview-MOC]]"
related: ["[[implementation-plan]]", "[[decisions-and-open-questions]]", "[[bin-structure-spec]]"]
---

# Overview & Scope

## Purpose

`market_data_dwndr` is a data-capture tool for Zerodha Kite. It has three jobs:

1. **Live index capture** — subscribe over the Kite WebSocket, assemble a 101-strike
   option chain (ATM ± 50) per index, and persist a snapshot **every second (1 Hz)**
   to a compact binary file (`.bin`), compressed with zstd at end of day. Depth = **L1**.
2. **Live stock capture** — for each F&O stock, capture raw ticks for its **spot + 3
   nearest futures** (the CalSpread "board") at 1 Hz, as a **matrix** frame. Depth = **L5**.
3. **Historical download** — pull historical OHLC + OI candles for chosen
   instruments/intervals and store them in the same `.bin` format.

Everything stored is **raw API data in native integer form** (prices as paise). All
engineered/derived values (Greeks, IV, net-change, change-in-OI, calendar spreads,
summary stats) are **not stored** — they are reconstructed on read from the raw fields
plus the day's bond yield (see [[bin-format]], [[stocks-capture]], [[lossless-and-precision]]).

## In scope

- Live option-chain tick capture (indices), 1 Hz, **L1** depth.
- Live stock capture: spot + 3 nearest futures per F&O stock, 1 Hz, **L5** depth ([[stocks-capture]]).
- ATM-centered strike-window selection identical to `algo_engine` (101 strikes, [[option-chain-selection]]).
- **Integer-native** binary frame storage (our own schema, [[bin-structure-spec]]) with
  the same framing approach and zstd level as `algo_engine`.
- Historical candle download with chunking, rate limiting, retries, and resume ([[historical-data]]).
- A Next.js/TypeScript frontend: reused option-chain UI + a **Capture Monitor**
  dashboard showing what is being saved ([[frontend]]).
- A Python backend (FastAPI) exposing REST + WebSocket endpoints and running the
  capture/download workers.

## Out of scope (explicit non-goals)

- **No order placement or management.** No execution, positions, P&L, risk, strategies.
- **No IV or Greeks computed at capture.** They are reconstructed on read for display
  only (Black-Scholes + the stored bond yield).
- **No automated RBI/bond-yield fetch.** The 10-yr yield is **entered manually at
  login** and stored in each file header (it is *not* fetched from an API).
- **No backtest playback engine.** We produce `.bin` files; replay is a later add-on.

## What we keep vs. drop (relative to algo_engine)

| Concern | algo_engine | market_data_dwndr |
|---|---|---|
| Option-chain selection (ATM ± 50) | ✅ | ✅ (identical logic) |
| Live WebSocket tick ingest | ✅ | ✅ (KiteTicker) |
| Binary frame storage + zstd | ✅ | ✅ own integer-native schema |
| Historical candle download | ✅ | ✅ (same `.bin` format) |
| Frontend option-chain UI | ✅ | ✅ (Greeks columns computed on read) |
| F&O stock spot + futures capture | ❌ | ✅ added (CalSpread board, L5) |
| Implied volatility / Greeks | ✅ stored | ❌ not stored — reconstructed on read |
| Risk-free / 10-yr bond yield | ✅ (RBI rate) | ✅ stored in header (manual daily entry) |
| VIX (raw from API) | ✅ | ✅ kept (raw) |
| Order execution / strategies / risk | ✅ | ❌ dropped |

## Core difference in transport

Live data is fetched over the **WebSocket only** (push-based). REST is used solely for
one-time bootstrap (instrument dump, reference spot/VIX) and historical candles —
never for the live tick stream. See [[implementation-plan]].
