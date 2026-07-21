---
title: Option Chain Selection
area: live-capture
type: spec
status: locked
tags: [area/live-capture, type/spec, status/locked]
up: "[[Live-Capture-MOC]]"
related: ["[[live-data-pipeline]]", "[[bin-structure-spec]]", "[[algo-engine-findings]]"]
---

# Option Chain Selection

Ported **as-is** from `algo_engine` to capture the same 101-strike window. Source:
`oc_maker/table/filter.rs` and `oc_maker/table/assembler.rs` ([[algo-engine-findings]]).

## Window definition

- `STRIKES_PER_SIDE = 50`
- The captured chain is **ATM ± 50 strikes = up to 101 strikes** (fewer only if the
  instrument master lacks that many strikes on one side).

## ATM computation

`get_spot_atm(spot, step)` rounds the spot to the nearest `step`:
```
base      = (floor(spot) // step) * step
remainder = floor(spot) % step
atm       = base + (0 if remainder < step/2 else step)
```
`step` is per-underlying (see table below), not hard-coded to 50.

## Selection algorithm (`option_chain_filter`)

1. Collect every strike across calls + puts for the chosen expiry into a sorted,
   de-duplicated list (integer paise keys — no float-key instability).
2. **Guard:** empty strike list → hard error.
3. Find the ATM strike via binary search; if the exact ATM isn't listed, pick the
   nearest available strike.
4. Window: `start = max(0, atm_idx - 50)`, `end = min(len-1, atm_idx + 50)`.
5. Keep only calls/puts whose strike falls in that window.

## Chain assembly (`build_option_chain_metadata_only`)

1. Resolve reference prices: **spot** and **VIX** (short WS read or a REST LTP call).
2. Resolve the **expiry**: caller-supplied, else `find_nearest_expiry`.
3. Split instruments into CE/PE for `(underlying, expiry)`.
4. Run `option_chain_filter` → windowed calls/puts + effective ATM.
5. Build a strike-sorted structure; the fixed strike vector goes in the file header
   ([[bin-structure-spec]]).
6. Build the **token map**: `instrument_token → role` (`Option{side, index}` | `Spot`
   | `Vix`) — the O(1) tick-apply index.

## Guards / validation to preserve

- Empty strike list → hard error.
- Spot must be > 0 before assembly (bootstrap must succeed).
- Instrument master must contain the `(underlying, expiry)` contracts.
- Token map includes only in-window contracts + the spot & VIX tokens (stray ticks are
  counted "unmatched" and ignored).

## Indices in scope (locked)

Per-underlying config: ATM `step`, options exchange, spot symbol/token.

| Underlying | Strike step | Options exch. | Spot symbol | Spot token |
|---|---|---|---|---|
| NIFTY | 50 | NFO | NSE:NIFTY 50 | 256265 |
| BANKNIFTY | 100 | NFO | NSE:NIFTY BANK | 260105 |
| FINNIFTY | 50 | NFO | NSE:NIFTY FIN SERVICE | 257801 |
| SENSEX | 100 | BFO | BSE:SENSEX | 265 |

> MIDCPNIFTY and BANKEX are **excluded** (per decision, [[decisions-and-open-questions]]).
> VIX: `NSE:INDIA VIX` (token 264969) — stored raw per chain.
> Each index gets its own file: `MARKET_DATA/INDICES/<INDEX>/<date>.bin` ([[storage-layout]]).

## Port notes

- Represent each option table with **NumPy integer arrays** per column (one per side,
  per field), indexed by strike position — mirrors the Rust columnar `Block`.
- Strikes are stored as integer paise keys for exact lookups.
- Greeks/IV are **not** stored; reconstructed on read ([[bin-format]]).
