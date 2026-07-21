---
title: Implementation Plan
area: overview
type: plan
status: locked
tags: [area/overview, type/plan, status/locked]
up: "[[Overview-MOC]]"
related: ["[[overview-and-scope]]", "[[tech-stack-and-efficiency]]", "[[bin-structure-spec]]", "[[live-data-pipeline]]", "[[frontend]]"]
---

# Implementation Plan

## Guiding principle: push-based, not pull-based

Live capture is **WebSocket push-based only** (KiteTicker). REST is used *only* for
bootstrap (instruments, reference spot/VIX) and historical candles — never the live
tick stream.

## Repository layout

```
market_data_dwndr/
├── docs/            # knowledge & plan (domain folders) — Obsidian content
├── logs/            # progress log + change log
├── repo-map/        # Obsidian vault hub: MOCs, tag index, graph entry
├── backend/
│   ├── pyproject.toml
│   └── app/
│       ├── main.py            # FastAPI app + lifespan
│       ├── config.py          # pydantic settings (.env: KITE_API_KEY/SECRET, MARKET_DATA_PATH)
│       ├── kite/
│       │   ├── auth.py        # login flow: api_key/secret -> request_token -> access_token
│       │   ├── instruments.py # instrument dump fetch + daily archive
│       │   ├── ticker.py      # KiteTicker bridge -> asyncio.Queue
│       │   └── historical.py  # REST historical download
│       ├── chain/
│       │   ├── filter.py      # get_spot_atm + option_chain_filter ([[option-chain-selection]])
│       │   ├── assembler.py   # build per-index table + token map
│       │   └── table.py       # NumPy integer columnar OptionTable
│       ├── stocks/
│       │   ├── board.py       # CalSpread board discovery
│       │   └── matrix.py      # NumPy stock matrix (spot + 3 futures, L5)
│       ├── bin_codec/
│       │   ├── writer.py      # frame-by-frame writer ([[bin-structure-spec]])
│       │   ├── reader.py      # reader + day index
│       │   ├── layout.py      # struct/numpy field layout (single source of truth)
│       │   └── compress.py    # zstd L17 EOD sweep
│       ├── capture/
│       │   ├── engine.py      # 1 Hz snapshot loop, per-underlying tables
│       │   └── monitor.py     # capture-status metrics for the dashboard
│       ├── historical/        # jobs, windows, limiter, storage (same .bin)
│       └── ws/                # tagged-envelope protocol + routes
├── frontend/         # Next.js app (reused components + Capture Monitor)
└── MARKET_DATA/      # output (gitignored) — see [[storage-layout]]
```

## `.env` auth + morning start

`.env` holds `KITE_API_KEY`, `KITE_API_SECRET`, `MARKET_DATA_PATH`. Kite issues a
**daily `access_token`** (resets ~06:00). Morning start (~06:30): open the login link →
authorize → the app exchanges the `request_token` for the day's `access_token` (using
the secret) and caches it. The **same screen captures the 10-yr bond yield**, which is
written into every file header that day.

## Phases (BIN codec first — everything depends on it)

### Phase 0 — Scaffold + vault
Backend skeleton (FastAPI, `.env` config), frontend skeleton, this `docs/` vault +
`logs/` + `repo-map/`.

### Phase 1 — BIN codec ([[bin-structure-spec]])
`bin_codec` writer + reader with `struct` + NumPy + `zstandard`; round-trip tests
(write → read → identical integers); zstd `.zst` re-index test.

### Phase 2 — Kite integration + discovery
`.env` auth/login; instrument dump + daily archive; option-chain assembly
([[option-chain-selection]]); CalSpread board discovery ([[stocks-capture]]).

### Phase 3 — Live capture
KiteTicker → asyncio bridge; per-underlying NumPy tables; 1 Hz snapshot; writer
threads; reconnect/backoff (indices L1, stocks L5). See [[live-data-pipeline]].

### Phase 4 — Interactive frontend
**Capture Monitor** dashboard (per-underlying WS health, frames written, file size,
1 Hz heartbeat, disk) + reused live option-chain / stocks views. See [[frontend]],
[[websocket-protocol]].

### Phase 5 — EOD compression + rollover
zstd L17 sweep at close; file rotation; instrument archive.

### Phase 6 — Historical download
REST historical, windowing, rate-limit, resume; same `.bin` format ([[historical-data]]).

### Phase 7 — Reconstruction + hardening
Greeks-on-read (Black-Scholes + header bond yield), CalSpread metrics rebuild;
logging, tests, docs finalize.

## Verification checkpoints

- Chain filter produces exactly the ATM ± 50 window on sample instrument data.
- `.bin` round-trips: write → read → identical integer arrays.
- Compressed `.bin.zst` decompresses and re-indexes correctly.
- Historical job resumes from a mid-run checkpoint without duplicate rows.
