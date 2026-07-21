---
title: Progress Log
area: logs
type: log
status: living
tags: [area/logs, log/progress, status/living]
up: "[[Logs-MOC]]"
related: ["[[change-log]]", "[[implementation-plan]]"]
---

# Progress Log

Newest first. One entry per working session.

---

## 2026-07-21 — Frontend built + algo_engine cross-verification

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- **Cross-verified** the Python ports against the `algo_engine` Rust source; confirmed
  parity for the ATM filter, Greek normalization, max-pain/PCR, reconnect policy, and
  bin export; fixed 3 gaps in `reconstruct/` (365.25-day year, intrinsic-value
  tolerance, VIX fallback IV). See [[change-log]].
- **Backend Broadcaster** (`app/capture/broadcaster.py`) — reconstructs IV/Greeks and
  pushes `MarketHeader`/`OptionGrid` (market-data), `StockBoard` (stocks),
  `CaptureStatus` (capture-status), `Heartbeat` (session); wired into the engine loop.
- **Next.js 16 frontend** under `frontend/` (React 19, Tailwind v4), ported from
  `algo_engine/frontend_stack` and trimmed to capture-only:
  - `lib/` — per-topic WebSocket connection (reconnect/backoff), envelope types, hooks,
    en-IN number formatting.
  - `/monitor` — per-underlying health, frames, file size, 1 Hz heartbeat, globals, log.
  - `/option-chain` — `OptionChainTable` with reconstructed IV/Greeks, spot/ATM/max-pain
    markers, index selector, keyframe + delta patching.
  - `/stocks` — F&O board matrix (spot + 3 futures) with live/daily calendar spreads.
  - `next build` (Turbopack) + `eslint` (flat config) both clean.
- Backend: 139 pytest tests green; ruff clean.

**Follow-ups**
- Live end-to-end validation against real Kite credentials.

---

## 2026-07-21 — Phase 7: Reconstruction + hardening (project build complete)

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- `reconstruct/bs.py` — Black-Scholes price / Greeks (theta per day, vega+rho per 1%) /
  implied vol (Newton + bisection). Matches textbook reference within 1e-3; IV
  round-trips within 1e-4.
- `reconstruct/greeks.py` — per-strike IV+Greeks for an `IndexFrame` from stored raw +
  header bond yield; time-to-expiry from `expiry_date`; `change = ltp − ohlc_close`.
- `reconstruct/metrics.py` — ATM (round to step), max-pain, PCR (OI/volume).
- `reconstruct/spreads.py` — CalSpread live/daily spread + summary (mean, min/max,
  mean-deviation, std-dev, p95, mean-reversion probability).
- `ops/retention.py` — storage report + `.zst` integrity spot-check (decode +
  monotonic timestamps); `logging_config.py`.
- 131 pytest tests (green) + ruff clean.

**All phases (0–7) complete.** Backend + BIN codec + Kite discovery + live capture +
Capture Monitor + EOD/rollover + historical downloader + reconstruction are implemented
and tested. The only paths not exercisable in CI are the live Kite WS/REST calls (need
credentials); they are covered by mocks/fixtures + a synthetic tick stream.

**Follow-ups**
- Full Next.js port of reused algo_engine `/option-chain` and `/stocks` pages (source
  not in this repo; `/monitor` shipped standalone).
- Live end-to-end validation once Kite credentials are available.

---

## 2026-07-21 — Phase 6: Historical downloader

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- `historical/intervals.py` (policy table), `windows.py` (chunking, clamp),
  `request.py` (validation guards: from<to, span≤max_ui_days, weekly/monthly &
  atm/strike-range exclusivity, expiry format), `limiter.py` (async token bucket,
  injectable clock), `client.py` (windowed fetch, candle parse, 429/5xx retry+backoff).
- `historical/assembly.py` — candle→frame assembly (bin_export pattern) for index
  chains + stock legs, `INDICES_HIS/` & `STOCKS_HIS/` writers (round-trips via reader).
- `historical/jobs.py` — `JobStore` (request + per-contract checkpoints under
  `_state/`), `HistoricalJob` (bounded-concurrency download, resume by skipping
  completed windows → no duplicate rows, cancel, progress via `historical-jobs` WS).
- 118 pytest tests (green) + ruff clean.

**Next**
- **Phase 7: Reconstruction + hardening** (Greeks/IV on read, retention, final polish).

---

## 2026-07-21 — Phase 5: EOD compression + rollover + session-state

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- `ops/calendar.py` — `TradingCalendar`: IST trading date (epoch ms → IST), weekend +
  configurable holiday handling, session phase (PRE_OPEN/OPEN/CLOSED/HOLIDAY) with
  09:15–15:30 inclusive boundaries; fixed +05:30 fallback if no tzdata.
- `ops/scheduler.py` — `PhaseMachine` (idempotent transition events) + `CaptureScheduler`
  driving start-capture / stop-capture / run-EOD callbacks.
- `ops/eod.py` — `run_eod` (stop writers → verify-and-compress sweep, raw removed only
  after `.zst` verifies), `prune_stale_raw` startup cleanup, `EODResult` with ratio.
  Only `*.bin` touched; `_instruments/`, `_state/` left alone.
- `ops/session_manager.py` — `SessionManager`: login once then resume today's session
  on restart (no re-prompt); mid-day restart appends to today's files with no duplicate
  header (verified end-to-end).
- 97 pytest tests (green) + ruff clean.

**Next**
- **Phase 6: Historical downloader** ([[build-guide]]).

---

## 2026-07-21 — Phase 4: Capture Monitor (WS protocol + monitor + dashboard)

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- `ws/protocol.py` — tagged-envelope `{type,payload}` builders: `MarketHeader`,
  `OptionGrid` (keyframe), `OptionGridDelta` (sparse changed-strike patch),
  `CaptureStatus`, `Heartbeat`, `SessionStatus`, `Log`, `HistoricalJobUpdate`;
  paise→rupees for display; `GridBlock` from `RawBlock`.
- `ws/routes.py` — `ConnectionManager` broadcast hub + `/ws/{topic}` endpoints with
  `?token=` auth (topics: market-data, stocks, capture-status, session,
  historical-jobs); wired into `app.main` (`app.state.ws_hub`).
- `capture/monitor.py` — `CaptureMonitor`: per-underlying (connected, last tick,
  frames, file bytes, 1 Hz heartbeat, unmatched) + global (unique tokens, fps, disk
  usage); writer thread now records `last_write_ms`.
- `app/static/monitor.html` + `/monitor` route — self-contained live dashboard
  (no build step) consuming `/ws/capture-status` and `/ws/session`.
- 87 pytest tests (green) + ruff clean.

**Deferred**
- Full Next.js port of reused algo_engine `/option-chain` and `/stocks` pages (those
  components are not in this repo).

**Next**
- **Phase 5: EOD compression + rollover + session-state** ([[build-guide]]).

---

## 2026-07-21 — Phase 3: Live capture

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- `kite/ticks.py` + `kite/ticker.py` — tick-field extraction (rupees→paise, OHLC,
  L1/L5 depth) and a KiteTicker→`asyncio.Queue` bridge (thread callbacks bridged with
  `call_soon_threadsafe`, `full`-mode subscribe on connect, overflow drops oldest).
- `chain/table.py` (`IndexTable`, L1) + `stocks/matrix.py` (`StockMatrix`, L5) —
  in-place O(1) token→index apply, unmatched counter, copy-on-snapshot to
  `IndexFrame`/`StockFrame`.
- `capture/writer_thread.py` (thread-per-file), `capture/reconnect.py`
  (`ReconnectPolicy` 5s→300s/20 attempts + `StallDetector` 30s), `capture/engine.py`
  (`CaptureEngine`: multi-owner routing so VIX fans out to every index, `capture_once`
  1 Hz snapshot→writer queues, async run loop).
- 69 pytest tests (green) + ruff clean. End-to-end (synthetic): apply→snapshot→`.bin`
  grows→reader replays both index (L1) and stock (L5) files.

**Next**
- **Phase 4: Interactive frontend (Capture Monitor)** — WS tagged-envelope protocol,
  `CaptureStatus` metrics, dashboard ([[build-guide]]).

**Blockers**
- None for coding. Live WS end-to-end needs Kite credentials.

---

## 2026-07-21 — Phase 2: Kite integration + discovery

**Done** (all on `ai-dev/made`, pushed batch-by-batch)
- `app/session.py` + `kite/auth.py` — login URL, SHA-256 checksum, injectable token
  exchange, and daily session-state persistence/resume (`_state/session-<date>.json`)
  holding `access_token` + bond yield.
- `kite/instruments.py` — instrument-dump parse (typed `Instrument`), injectable HTTP
  fetcher, and daily archive to `_instruments/<date>/<EXCH>.csv` with cache/refresh.
- `chain/config.py`, `chain/filter.py`, `chain/assembler.py` — per-index config
  (locked 4 indices), `get_spot_atm`, `option_chain_filter` (exact ATM ± 50 window,
  integer paise keys, empty-strike guard), and chain assembly producing the fixed
  strike vector + `token -> Role` map.
- `stocks/board.py` — CalSpread board discovery (NFO FUT names matched to NSE EQ
  spots, indices excluded, 3 nearest futures), `StockHeader` refs, and a
  `token -> (row, leg)` routing map.
- 51 pytest tests total (green) + ruff clean.

**Next**
- **Phase 3: Live capture** — KiteTicker→asyncio bridge, NumPy tables/matrix, 1 Hz
  snapshot engine + writer threads ([[build-guide]]).

**Blockers**
- None for coding. Phase 2 DoD's *live* checks (real access_token, real instrument
  data) need Kite credentials; logic is verified against fixtures/mocks.

---

## 2026-07-21 — Phase 0 scaffold + Phase 1 BIN codec

**Done**
- **Phase 0:** backend skeleton on `ai-dev/made` — `backend/pyproject.toml`,
  `app/main.py` (FastAPI + `/health`), `app/config.py` (pydantic-settings with derived
  `MARKET_DATA` paths), `.env.example`. `/health` verified via TestClient. Frontend
  skeleton deferred to Phase 4 (per [[next-session-handoff]]).
- **Phase 1 (BIN codec):** implemented exactly per [[bin-structure-spec]] with
  `struct` + NumPy + `zstandard`:
  - `bin_codec/layout.py` — primitives, enum tags, LE dtypes, fixed column order,
    frame data models (single source of truth).
  - `bin_codec/writer.py` — `[u32 len][payload]` framing, header-once, index + stock
    encoders and append-only writers.
  - `bin_codec/reader.py` — one-pass scan → `timestamp → (offset,size)` index,
    nearest-ts binary search, random-access ranges, mmap raw / transparent `.zst`,
    truncated-trailing-frame recovery.
  - `bin_codec/compress.py` — whole-file zstd L17 → `.bin.zst`, verified raw removal,
    EOD directory sweep.
- **Tests (23, all green) + ruff clean.** Phase 1 DoD gates pass: round-trip
  identical integer arrays (index + stock), byte-level header check, and
  compress → re-index → identical. See [[testing-strategy]].
- Pushed batch-by-batch to `ai-dev/made`.

**Next**
- Open a PR `ai-dev/made` → `main` for Phase 0 + Phase 1 review.
- **Phase 2: Kite integration + discovery** ([[build-guide]]).

**Blockers**
- None. Phases 2+ need live Kite credentials for end-to-end verification; unit tests
  will mock Kite.

---

## 2026-07-21 — Docs finalized + phase build guide

**Done**
- Filled all gaps: [[build-guide]] (phase/batch DoD checklist), operations domain
  ([[operations-runbook]], [[config-and-env]], [[session-state]], [[failure-modes]],
  [[data-retention]]), [[testing-strategy]], [[reconstruction]].
- Wired into the vault (Operations/Quality MOCs, Home/Tags updated); verified links.
- Preparing branch `ai-dev/made` and pushing the knowledge base to the remote.

**Next**
- Fresh session → **Phase 1: BIN codec** ([[build-guide]]).

**Blockers**
- None.

## 2026-07-21 — Planning & knowledge base

**Done**
- Explored `algo_engine` (BIN writer/reader/compressor, option-chain selection,
  historical `bin_export`, frontend) and CalSpread (stock board discovery, price
  sources, metrics). See [[algo-engine-findings]], [[stocks-capture]].
- Locked the full design: integer-native BIN format ([[bin-structure-spec]]), 1 Hz
  cadence, indices L1 / stocks L5, bond yield in header, Greeks reconstructed on read.
- Authored the knowledge base (17 domain notes) and reorganized it into an Obsidian
  vault: `docs/` (domain folders) + `logs/` + `repo-map/` (MOCs).

**Next**
- Phase 1 build: `bin_codec` writer + reader with round-trip tests ([[implementation-plan]]).

**Blockers**
- None. All blocking decisions are resolved ([[decisions-and-open-questions]]).
