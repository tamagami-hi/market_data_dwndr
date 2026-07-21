---
title: Home
area: map
type: moc
status: living
tags: [area/map, type/moc]
---

# 🏠 market_data_dwndr — Home

> [!abstract] What this is
> A Zerodha Kite **market-data downloader** (capture only — no trading). It records live
> index option chains (ATM ± 50, L1) and F&O stock calendar-spread boards (L5) at **1 Hz**
> into an integer-native binary format, plus historical OHLC+OI candles, and reconstructs
> IV/Greeks/metrics on read. A Next.js dashboard monitors capture and renders the chains.

Open the **repo root** (`market_data_dwndr/`) as an Obsidian vault so wikilinks resolve
across `docs/`, `logs/`, `repo-map/`, and the code under `backend/` + `frontend/`.
New here? Read [[vault-guide]].

> [!success] Project status — **build complete**
> All phases 0–7 implemented and tested; frontend built; automated login wired.
> Backend: **159 tests green, ruff clean**. Frontend: `next build` + `eslint` clean.
> Details: [[Build-Status]] · [[progress-log]].

## 🚦 Start here
1. [[overview-and-scope]] — what's in / out of scope
2. [[bin-structure-spec]] — the authoritative byte format (the foundation)
3. [[live-data-pipeline]] → [[stocks-capture]] — how capture works
4. [[Code-Map]] — docs ↔ source (where each spec lives in code)
5. [[operations-runbook]] — run it day-to-day
6. [[decisions-and-open-questions]] — the locked decisions

## 🗺️ Maps of Content
| MOC | Domain | Implemented in |
|-----|--------|----------------|
| [[Overview-MOC]] | scope, plan, build guide | — |
| [[Architecture-MOC]] | stack, concurrency/GIL | `backend/app/capture/` |
| [[Data-Storage-MOC]] | BIN format, layout, precision, reconstruction | `backend/app/bin_codec/`, `reconstruct/` |
| [[Live-Capture-MOC]] | chain selection, pipeline, stocks, perf | `backend/app/{kite,chain,stocks,capture}/` |
| [[Historical-MOC]] | historical downloader | `backend/app/historical/` |
| [[Frontend-MOC]] | dashboard, monitor, WS protocol | `frontend/`, `backend/app/ws/` |
| [[Operations-MOC]] | runbook, config/env, session, auth, EOD | `backend/app/{ops,api}/`, `main.py` |
| [[Quality-MOC]] | testing strategy | `backend/tests/` |
| [[Decisions-MOC]] | locked decisions & open items | — |
| [[Reference-MOC]] | algo_engine + depth research | `algo_engine/` (external) |
| [[Logs-MOC]] | progress & change logs | `logs/` |

## 🧭 Cross-cutting maps
- [[Code-Map]] — every spec note → its module(s) and tests
- [[Build-Status]] — phase-by-phase status dashboard
- [[Tags]] — tag taxonomy for Graph groups

## 🔎 By concern
- **Prices are integers** — paise (×100); floats only for the bond yield → [[lossless-and-precision]]
- **Login is automated** — env-seeded creds + terminal/UI TOTP → [[config-and-env]], [[session-state]]
- **Nothing derived is stored** — Greeks/IV recomputed on read → [[reconstruction]]
- **Static-IP egress** (Kite, Apr 2026) → [[config-and-env]]
