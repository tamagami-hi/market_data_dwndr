---
title: Lossless Storage & Precision
area: data-storage
type: spec
status: locked
tags: [area/data-storage, type/spec, status/locked]
up: "[[Data-Storage-MOC]]"
related: ["[[bin-structure-spec]]", "[[bin-format]]", "[[live-capture-performance]]"]
---

# Lossless Storage & Numeric Precision

"Lossless" has two independent axes. We are explicit about both.

## Axis 1 — Temporal (which ticks we keep)

We capture at **1 Hz** (one snapshot/second, last-value-wins). This is a deliberate
*sample in time*: intra-second updates between snapshots are not retained. So
"lossless" here means **lossless per stored snapshot**, not "every tick preserved."
Rationale: efficiency + predictable volume ([[live-capture-performance]]). Zero tick
loss would be per-tick capture (much larger) — a separate mode.

## Axis 2 — Representational (each stored value bit-exact to the API)

Here we are strictly lossless: **store the API's native integers, never a divided
float.**

- Kite sends prices as integer **paise** (₹24567.05 → `2456705`), and quantities / OI
  / volume / timestamps as integers.
- `0.05` (the common NSE tick) is **not exactly representable in binary `f64`**, so
  dividing prices to `f64` introduces tiny rounding. Storing raw integer paise is
  **bit-exact** and unambiguous.

| Field | Stored as | Exact? |
|---|---|---|
| prices (ltp, bid, ask, OHLC, strike, spot, vix) | `i64` paise (`×100`) | ✅ |
| quantities, bid/ask qty, orders | `u64` / `u32` | ✅ |
| open interest (+ day hi/lo), volume | `u64` | ✅ |
| timestamp (ms), sequence, tokens | `u64` | ✅ |
| risk_free_rate (risk-free rate) | `f64` | manual scalar; exact enough |

Benefits beyond exactness: **smaller** and **better compression** (slowly-changing
integer columns).

## Other lossless properties

- **All raw fields stored; only computable fields dropped.** Greeks/IV, net `change`,
  `change_in_oi`, and CalSpread spreads/stats are reproducible from stored raw + the
  header risk-free rate — excluding them loses no information ([[bin-format]], [[stocks-capture]]).
- **zstd is lossless compression.** Level 17 affects only ratio/speed, never data.
- **Optional max-fidelity extra:** we can also store the exchange-provided timestamp
  per snapshot (Kite full mode carries `exchange_timestamp`) alongside our receive time.

## On read

Divide price columns by 100 (paise → rupees); compute Greeks/IV lazily via
Black-Scholes using the header `risk_free_rate` only when displaying.
