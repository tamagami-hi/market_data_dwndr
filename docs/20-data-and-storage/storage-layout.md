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

Raw capture data and operational state live under `MARKET_DATA_PATH`. Verified zstd
files live under the separate `ARCHIVE_DATA_PATH`, which mirrors the market-data
relative layout. Indices are stored per-index; stocks as a single matrix file per day.

```
MARKET_DATA/
├── INDICES/                         # live indices
│   ├── NIFTY/
│   │   └── 2026-07-21.bin           # raw, during the session
│   ├── BANKNIFTY/
│   ├── FINNIFTY/
│   └── SENSEX/
├── STOCKS/                          # live stocks — ONE matrix file per day (all stocks)
│   └── 2026-07-21.bin
├── INDICES_HIS/                     # historical indices (same format as live)
│   ├── NIFTY/
│   │   └── 2026-01-15.bin
│   └── BANKNIFTY/
├── STOCKS_HIS/                      # historical stocks (matrix format)
│   └── 2026-01-15.bin
├── _instruments/                    # daily instrument-master snapshots
│   └── 2026-07-21/
│       ├── NFO.csv
│       ├── NSE.csv
│       └── BFO.csv
└── _meta/                           # run logs / capture manifests (optional)
```

After EOD, the market-data paths move to the archive root with a `.zst` suffix:

```text
ARCHIVE_DATA_PATH/INDICES/NIFTY/2026-07-21.bin.zst
ARCHIVE_DATA_PATH/STOCKS/2026-07-21.bin.zst
ARCHIVE_DATA_PATH/INDICES_HIS/NIFTY/2026-01-15.bin.zst
ARCHIVE_DATA_PATH/STOCKS_HIS/2026-01-15.bin.zst
```

## Conventions

- **Filename = `{YYYY-MM-DD}.bin`** (compressed: `{YYYY-MM-DD}.bin.zst`). The date is
  the whole stem; the folder (or root) identifies index-vs-stock and live-vs-historical.
- **Indices:** one folder per index under `INDICES/` (live) and `INDICES_HIS/`
  (historical). One file per trading day, `IndexHeader` first ([[bin-structure-spec]]).
- **Stocks:** a **single file per day** under `STOCKS/` (live) and `STOCKS_HIS/`
  (historical) holding **all** stocks as a matrix, `StockHeader` first ([[stocks-capture]]).
- **Compression:** raw `.bin` during the session; whole file → `.bin.zst` (zstd L17)
  under `ARCHIVE_DATA_PATH` at end of day. The raw file is removed only after the
  archive is streamed, verified, and atomically published on its destination disk.
- **Bond yield:** the day's 10-yr yield (entered at login) is written into every
  file's header so each file is self-contained and Greeks are reconstructable.

## Instrument-master archive (`_instruments/`)

Daily snapshot of the Kite instrument dump per exchange (`NFO`, `NSE`, `BFO`). Needed
to (a) reconstruct the exact board/ATM window for any past day, and (b) resolve
expired option/future tokens later (they vanish from the live dump).

## Path-safe symbols

The stock file name is date-based, so symbols like `M&M` / `BAJAJ-AUTO` never touch
the path; the true `tradingsymbol` is preserved verbatim in the `StockHeader`.
