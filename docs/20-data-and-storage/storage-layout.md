---
title: Storage Layout
area: data-storage
type: spec
status: locked
tags: [area/data-storage, type/spec, status/locked]
up: "[[Data-Storage-MOC]]"
related: ["[[bin-structure-spec]]", "[[bin-format]]", "[[historical-data]]", "[[stocks-capture]]"]
---

# Storage Layout

All captured data lives under a single `MARKET_DATA/` root (configurable path).
Indices are stored per-index; stocks as a single matrix file per day. Historical data
lives in separate `*_HIS` roots.

```
MARKET_DATA/
├── INDICES/                         # live indices
│   ├── NIFTY/
│   │   ├── 2026-07-21.bin           # raw, during the session
│   │   └── 2026-07-20.bin.zst       # zstd L17, after EOD compression
│   ├── BANKNIFTY/
│   ├── FINNIFTY/
│   └── SENSEX/
├── STOCKS/                          # live stocks — ONE matrix file per day (all stocks)
│   ├── 2026-07-21.bin
│   └── 2026-07-20.bin.zst
├── INDICES_HIS/                     # historical indices (same format as live)
│   ├── NIFTY/
│   │   └── 2026-01-15.bin.zst
│   └── BANKNIFTY/
├── STOCKS_HIS/                      # historical stocks (matrix format)
│   └── 2026-01-15.bin.zst
├── _instruments/                    # daily instrument-master snapshots
│   └── 2026-07-21/
│       ├── NFO.csv
│       ├── NSE.csv
│       └── BFO.csv
└── _meta/                           # run logs / capture manifests (optional)
```

## Conventions

- **Filename = `{YYYY-MM-DD}.bin`** (compressed: `{YYYY-MM-DD}.bin.zst`). The date is
  the whole stem; the folder (or root) identifies index-vs-stock and live-vs-historical.
- **Indices:** one folder per index under `INDICES/` (live) and `INDICES_HIS/`
  (historical). One file per trading day, `IndexHeader` first ([[bin-structure-spec]]).
- **Stocks:** a **single file per day** under `STOCKS/` (live) and `STOCKS_HIS/`
  (historical) holding **all** stocks as a matrix, `StockHeader` first ([[stocks-capture]]).
- **Compression:** raw `.bin` during the session; whole file → `.bin.zst` (zstd L17)
  at end of day, raw removed.
- **Bond yield:** the day's 10-yr yield (entered at login) is written into every
  file's header so each file is self-contained and Greeks are reconstructable.

## Instrument-master archive (`_instruments/`)

Daily snapshot of the Kite instrument dump per exchange (`NFO`, `NSE`, `BFO`). Needed
to (a) reconstruct the exact board/ATM window for any past day, and (b) resolve
expired option/future tokens later (they vanish from the live dump).

## Path-safe symbols

The stock file name is date-based, so symbols like `M&M` / `BAJAJ-AUTO` never touch
the path; the true `tradingsymbol` is preserved verbatim in the `StockHeader`.
