---
title: Live Capture Performance
area: live-capture
type: spec
status: locked
tags: [area/live-capture, type/spec, status/locked]
up: "[[Live-Capture-MOC]]"
related: ["[[live-data-pipeline]]", "[[bin-structure-spec]]", "[[tech-stack-and-efficiency]]", "[[stocks-capture]]"]
---

# Live Capture Performance (1 Hz, integer-native, indices L1 / stocks L5)

Capture is **one snapshot per second (1 Hz)** for every index (101-strike chain, L1)
and all F&O stocks (matrix: spot + 3 futures, L5). Ticks are applied continuously; a
1-second timer writes the latest state as one frame. Last-value-wins per second.

## Frame size (raw, integer-native)

- **Index frame (L1, 101 strikes):** 15 columns × 2 sides, each `101 × 8` bytes +
  headers ≈ **~24.5 KB**.
- **Stock frame (L5, ~200 stocks × spot+3 futures):** scalar columns + 5-level depth
  arrays ≈ **~0.26 MB**.

## Daily volume (market ≈ 6.25 h ≈ 22,500 s ⇒ 22,500 frames/day at 1 Hz)

| Stream | raw/day each | note |
|---|---|---|
| 1 index | ~0.55 GB | (matches your tested ~900 MB → ~250 MB compressed on the older 23-col f64 layout; our leaner integer L1 layout is smaller) |
| 4 indices | ~2.2 GB | → sub-GB compressed |
| all ~200 stocks (L5) | ~5.8 GB | → ~1–1.5 GB compressed |

**Total ≈ 8 GB/day raw → roughly ~1.5–2.5 GB/day compressed.** Within the agreed
budget (server sustains ~1 GB/day for years; more storage planned).

## Why this is comfortable

- **CPU negligible:** ~204 small frames/second, serialized via NumPy `tobytes`
  (integer LE layout = wire layout) — sub-100 µs/frame. No per-tick Greeks.
- **Writes trivial:** one small append per file per second. One writer thread per file.
- **zstd off the hot path:** whole-file L17 at EOD, parallelizable across files (the C
  extension releases the GIL).

## Architecture (simplified for 1 Hz)

```
KiteTicker (one WS conn, ~1,600 tokens) ──on_ticks──► asyncio bridge
        │ route each tick by token → its underlying's table / stock row
        ▼
   [ index tables (L1) ]   [ stock matrix (L5) ]
        │ 1 Hz timer: snapshot each → frame
        ▼
   per-file writer thread ──► <date>.bin (append)
        │ at market close
        ▼
   EOD zstd L17 sweep → <date>.bin.zst
```

- **One WS connection** covers the ~1,600-token universe (< 3,000 limit).
- **Ops note:** ~204 open file handles (4 indices + 1 stocks file... actually 4 index
  files + 1 stock matrix file = 5 files). Trivial. (Historical jobs open additional
  files transiently.)
- Memory: index tables ~tens of KB each; the stock matrix a few MB. Negligible.

## Sizing depends on the depth decision

Indices staying **L1** keeps the chain files small; **L5 for stocks** is the main
volume driver but is where order-book depth actually helps arbitrage
([[depth-level-research]]). Both confirmed in [[decisions-and-open-questions]].
