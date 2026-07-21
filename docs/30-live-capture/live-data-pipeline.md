---
title: Live Data Pipeline
area: live-capture
type: spec
status: locked
tags: [area/live-capture, type/spec, status/locked]
up: "[[Live-Capture-MOC]]"
related: ["[[option-chain-selection]]", "[[stocks-capture]]", "[[live-capture-performance]]", "[[bin-structure-spec]]", "[[tech-stack-and-efficiency]]"]
---

# Live Data Pipeline

End-to-end flow for live capture. Mirrors `algo_engine` minus Greeks; adds the stock
matrix. Greeks are never computed at capture — reconstructed on read ([[bin-format]]).

## Stages

```
1. Bootstrap   fetch instrument dump (+ daily archive) + reference spot/VIX
2. Assemble    per index: ATM ± 50 empty table + token map ([[option-chain-selection]])
               stocks: CalSpread board + matrix ([[stocks-capture]])
3. Subscribe   open Kite WebSocket (KiteTicker), subscribe all tokens in "full" mode
4. Ingest      KiteTicker on_ticks → asyncio queue → decode (continuous)
5. Apply       write raw tick fields into the columnar table/matrix at token→index
6. Capture     ONCE PER SECOND (1 Hz): snapshot latest state → frame
7. Persist     enqueue frame to the per-file .bin writer ([[bin-structure-spec]])
8. Broadcast   push slim frames + capture status to the frontend ([[websocket-protocol]])
```

## Bootstrap

- Load instrument masters for the needed exchanges (NFO for NSE index/stock options &
  futures, BFO for SENSEX, NSE for equity spots). Cache + daily archive ([[storage-layout]]).
- Resolve spot + VIX via a short WS read or a REST LTP call. Spot must be > 0.

## Subscription

- Universe ≈ 4 indices × ~202 option legs (~808) + ~200 stocks × (spot + 3 futures,
  ~800) ≈ **~1,600 tokens** — under Kite's 3,000/connection limit, so **one WS
  connection** suffices. Shard to a second connection only if the count grows.
- Subscribe then set mode `full` (delivers OI, OHLC, and 5-level depth).
- Depth retained: **indices L1**, **stocks L5** ([[depth-level-research]]).

## Ingest & apply

- Decode each tick via the `KiteTicker` callback (it parses the binary packet incl.
  5-level depth).
- Look up `instrument_token` in the token map:
  - `Option{side, idx}` → write raw fields into the CE/PE integer arrays at `idx` (L1).
  - stock leg → write into that stock's row in the matrix (L5 depth).
  - `Spot`/`Vix` → update the index scalars.
  - unknown token → increment an "unmatched" counter and ignore.
- **Raw only.** `change`, `change_in_oi`, IV, Greeks, and spreads are **not stored at
  all** (our own schema, [[bin-format]]) — reconstructed on read.

## Capture cadence — 1 Hz (locked)

Ticks are applied continuously; a **1-second timer** writes the *latest* state of each
table/matrix as one frame. Predictable volume, trivial CPU, wall-clock-aligned samples
(good for cross-instrument/arbitrage alignment), last-value-wins per second. See
[[live-capture-performance]].

## Concurrency model (Python)

- **Ingest:** KiteTicker's threaded callback bridged into an `asyncio.Queue`.
- **Apply:** inline (cheap NumPy integer writes).
- **Capture timer:** a 1 Hz task snapshots every table/matrix → per-file writer queue +
  broadcast channel.
- **Writer:** a dedicated thread per file (blocking I/O); near-idle at 1 Hz.
- **Compression:** EOD zstd L17 in a pool, off the hot path ([[tech-stack-and-efficiency]]).

## Resilience

- Reconnect with exponential backoff + circuit breaker (mirror `ReconnectPolicy`:
  base 5 s, max 300 s, ~20 attempts).
- Stall detection: no message for ~30 s → reconnect.
- On reconnect, re-subscribe the same token set; tables and writers persist.
- Market-hours aware: capture during market hours; roll files + compress at close.
