---
title: Code-Map
area: map
type: code-map
status: living
tags: [area/map, type/code-map]
up: "[[Home]]"
related: ["[[Build-Status]]", "[[build-guide]]", "[[Architecture-MOC]]"]
---

# 🧩 Code Map — docs ↔ source

Maps each specification note to the module(s) that implement it and the tests that
cover it. Paths are relative to the repo root. See [[Build-Status]] for phase status.

> [!info] Shape
> `backend/` — Python 3.11 / FastAPI service (48 modules).
> `frontend/` — Next.js 16 + React 19 + Tailwind v4.
> `backend/tests/` — 32 pytest modules, **178 tests**.

## Backend — package map

```
backend/app/
├── main.py                 FastAPI app: /health, /monitor, /api/auth, /ws/*, lifespan resume
├── config.py               pydantic-settings (env), derived MARKET_DATA paths
├── session.py              SessionState + load/save (daily access_token + risk-free rate)
├── session_service.py      app facade: status() / login() / login_url()
├── logging_config.py       central logging
├── bin_codec/              ← [[bin-structure-spec]] (the foundation)
│   ├── layout.py           primitives, enum tags, dtypes, column order, frame models
│   ├── writer.py           framed, header-once index/stock writers
│   ├── reader.py           scan → ts index, nearest-ts, ranges, mmap/.zst, truncation-safe
│   └── compress.py         whole-file zstd L17, verified raw removal
├── kite/                   ← Kite integration
│   ├── auth.py             checksum, auth header, KiteAuthenticator (resume/exchange)
│   ├── login.py            automated login (login→twofa TOTP→request_token→exchange), md-login
│   ├── instruments.py      instrument dump fetch/parse + daily archive
│   ├── ticks.py            full-tick field extraction (paise, OHLC, depth)
│   ├── ticker.py           KiteTicker → asyncio.Queue bridge
│   └── quotes.py           one-shot LTP quote (seeds ATM at bootstrap)
├── chain/                  ← [[option-chain-selection]]
│   ├── config.py           per-index config (step, tokens), VIX token
│   ├── filter.py           get_spot_atm, ATM ± 50 window, nearest-strike
│   ├── assembler.py        strike vector + token→role map
│   └── table.py            live L1 IndexTable (apply/snapshot)
├── stocks/                 ← [[stocks-capture]]
│   ├── board.py            CalSpread F&O board discovery + token routing
│   └── matrix.py           live L5 StockMatrix (apply/snapshot)
├── capture/                ← [[live-data-pipeline]]
│   ├── engine.py           1 Hz snapshot engine, routing, async run loop
│   ├── writer_thread.py    thread-per-file writer (+ heartbeat)
│   ├── reconnect.py        ReconnectPolicy + StallDetector
│   ├── monitor.py          CaptureMonitor telemetry (per-underlying + global)
│   ├── broadcaster.py      reconstructs Greeks → pushes MarketHeader/OptionGrid/StockBoard
│   ├── bootstrap.py        wire instruments→chains+board→engine/monitor/broadcaster→ticker
│   └── run.py              md-capture CLI (resume→bootstrap→run→EOD)
├── ws/                     ← [[websocket-protocol]]
│   ├── protocol.py         tagged-envelope builders
│   └── routes.py           ConnectionManager + /ws/{topic}
├── api/
│   ├── auth.py             /api/auth/status · /login · /login-url
│   └── capture.py          CaptureController + status/history/maintenance APIs
├── ops/                    ← [[operations-runbook]]
│   ├── calendar.py         IST trading date + session phase
│   ├── scheduler.py        phase machine → start/stop/EOD events
│   ├── eod.py              stop→verify→compress sweep, stale-raw prune
│   ├── session_manager.py  resume-or-login orchestration
│   └── retention.py        storage/history reports + .zst integrity check
├── historical/             ← [[historical-data]]
│   ├── intervals.py windows.py request.py limiter.py client.py assembly.py jobs.py
└── reconstruct/            ← [[reconstruction]]
    ├── bs.py               Black-Scholes price/Greeks/IV (algo_engine parity)
    ├── greeks.py           per-frame IV+Greeks from stored raw + risk-free rate
    ├── metrics.py          ATM / max-pain / PCR
    └── spreads.py          CalSpread live/daily spread + summary
```

## Frontend — map

```
frontend/
├── app/{layout,page}.tsx           shell + landing
├── app/monitor/page.tsx            Capture Monitor        ← capture-status, session
├── app/option-chain/page.tsx       chain + Greeks table   ← market-data
├── app/stocks/page.tsx             F&O board + spreads    ← stocks
├── app/login/page.tsx              downloader init/status view   ← /api/auth
├── components/                     NavBar, SessionBadge, ConnectionDot, OptionChainTable
└── lib/                            wsTopicConnection, wsTypes, useTopic, api, config, numberFormat
```

## Spec → code → tests

| Spec note | Module(s) | Tests |
|-----------|-----------|-------|
| [[bin-structure-spec]] | `bin_codec/*` | `test_layout`, `test_writer`, `test_roundtrip`, `test_compress` |
| [[reconstruction]] | `reconstruct/*` | `test_reconstruct_bs`, `test_reconstruct_metrics` |
| [[option-chain-selection]] | `chain/*` | `test_chain`, `test_table_matrix` |
| [[stocks-capture]] | `stocks/*` | `test_board`, `test_table_matrix` |
| [[live-data-pipeline]] | `kite/{ticks,ticker}`, `capture/*`, `chain/table` | `test_ticks`, `test_ticker`, `test_capture`, `test_monitor` |
| [[websocket-protocol]] | `ws/*`, `capture/broadcaster`, `frontend/lib/ws*` | `test_ws_protocol`, `test_ws_routes`, `test_broadcaster` |
| [[historical-data]] | `historical/*` | `test_historical_core`, `test_historical_assembly`, `test_historical_jobs` |
| [[operations-runbook]] | `ops/*`, `main.py`, `capture/{bootstrap,run}`, `api/capture` | `test_calendar_scheduler`, `test_eod`, `test_session_manager`, `test_bootstrap`, `test_capture_run`, `test_capture_api` |
| [[config-and-env]] / [[session-state]] | `config.py`, `kite/{login,quotes}`, `session*`, `api/auth` | `test_login`, `test_auth_api`, `test_quotes` |
| [[data-retention]] / [[failure-modes]] | `ops/retention`, `bin_codec/{reader,compress}` | `test_retention`, `test_roundtrip` |
