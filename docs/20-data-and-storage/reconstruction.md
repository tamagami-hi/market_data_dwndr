---
title: Reconstruction (Greeks & Metrics on Read)
area: data-storage
type: spec
status: locked
tags: [area/data-storage, type/spec, status/locked]
up: "[[Data-Storage-MOC]]"
related: ["[[bin-structure-spec]]", "[[bin-format]]", "[[stocks-capture]]", "[[lossless-and-precision]]"]
---

# Reconstruction (Greeks & Metrics on Read)

We store **raw only**; every derived value is recomputed **on read** from the stored
raw fields + the header's `risk_free_rate`. This module is *not* part of capture — it
runs when analyzing/displaying data.

## Inputs available in a `.bin`

- Per strike (index) / per contract (stock): `ltp, oi, volume, bid/ask (+qty), OHLC,
  oi_day_high/low` — as integer paise/counts ([[bin-structure-spec]]).
- Scalars: `spot_price`, `vix`, `timestamp`; header: `expiry_date`, `risk_free_rate`,
  `strikes`.
- Read step converts paise → rupees (÷100).

## Option Greeks & IV (indices)

- **IV:** solve Black-Scholes implied vol from option `ltp` (mid or LTP), `spot`,
  `strike`, time-to-expiry (from `expiry_date` + timestamp), and `risk_free_rate`.
- **Greeks:** delta/gamma/vega/theta/rho from the BS closed form using that IV. Port
  `algo_engine`'s `oc_maker/bs_models.rs` conventions (per-day theta, per-1% vega/rho).
- **Time-to-expiry:** use the trading/calendar DTE convention consistent with
  `algo_engine` ([[algo-engine-findings]]).
- **change / change_in_oi:** `change = ltp − ohlc_close`; `change_in_oi = oi −
  previous_day_close_oi` (prior day's last OI, from the previous `.bin`).

## Aggregate chain metrics (display only)

- **ATM** (round spot to `step`), **max-pain**, **PCR** (OI/volume) — pure aggregations
  over stored columns; no IV needed.

## CalSpread stock metrics

- **Live/hourly spread** = `fut_mid.ltp − fut_current.ltp`.
- **Daily spread** = `fut_mid.close − fut_current.close` (nearest two expiries).
- **Summary** (mean, min/max, mean-deviation, std-dev, 95th percentile, mean-reversion
  probability) — computed over a symbol's stored daily closes, mirroring CalSpread's
  `recomputeSpreadSummary` ([[stocks-capture]]).

## API shape (proposed)

- `reconstruct_greeks(frame, header) -> per-strike arrays` (lazy/cached).
- `reconstruct_chain_metrics(frame) -> {atm, max_pain, pcr_oi, pcr_volume}`.
- `reconstruct_stock_spreads(frames) -> spread series` and `summary(symbol)`.

## Guarantee

Because all raw inputs + the bond yield are stored, reconstruction is deterministic and
lossless-consistent — recomputing later yields the same values a live engine would,
without ever having persisted them.
