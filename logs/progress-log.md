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

## 2026-08-07 (later) — Six indices, index-F&O paths, and three separate clocks

Follow-up to the entry below, closing its three open decisions.

**All six indices are now supported and enabled by default.** MIDCPNIFTY and BANKEX were
added, reversing decision #9. Every value was verified against that day's live instrument
masters rather than assumed: spot tokens from the `INDICES` segment (`NIFTY MID SELECT` =
288009, `BANKEX` = 274441, which also re-confirmed SENSEX 265 — this required fetching the
BSE master, which the app had never pulled), and strike steps derived from the modal gap
between listed strikes (MIDCPNIFTY **25**, BANKEX **100**). Both fields fail silently if
wrong — they would centre the chain on the wrong strike — so guessing was not an option.

**Verified capacity rather than estimating it.** The six-index universe with index F&O is
**2,067 tokens**, computed from the real masters: six chains × 202 options + six spots +
shared VIX + 18 index futures + 830 stock tokens. That is 633 below the 2,700 safe threshold
and 69% of the broker's hard limit, so one connection still carries it. A guard test pins
the arithmetic so a widened strike window or an added expiry fails loudly instead of
silently losing a subscription.

**Storage paths** are `INDICES_FnO` live and, because the EOD sweep preserves the live
layout, `INDICES_FnO` under the archive root too — no separate archive wiring. The artifact
name was renamed to match the folder so a dashboard row and a directory read the same.

**Three clocks, none hardcoded.** Conflating them is what made a routine pre-open look like
a dead feed:

| concept | setting | deployment | governs |
|---|---|---|---|
| bootstrap | `BOOTSTRAP_TIME` | 08:55 | when the process may prepare |
| capture window | `CAPTURE_START_TIME`/`_END_TIME` | 09:10–15:30 | when the **process** runs |
| market session | `EQUITY_DERIV_OPEN`/`_CLOSE` | 09:15–15:30 | when a frame is **owed** |

The downloader starts at 09:10 so the socket and subscriptions are live before the first
print; those five minutes are reported as `unscheduled` and can never become data loss.
Extending `CAPTURE_END_TIME` past the session close keeps a post-close tail on the same
terms, entirely via the env file. Also removed the remaining hardcoded market assumptions:
the `23_400` expected-frame fallbacks now report 0 (an unknown baseline is honest; an
invented one silently corrupts every loss percentage), the auth-window fallback reads the
field defaults, and the frame baseline now derives from the **session** rather than the
wider capture window so day-progress agrees with the scheduled loss figures.

**Re-measured at six indices**: snapshot 0.20 ms per grid second (0.02% of budget), tick
apply 3.59 ms per second of flow, **8.92 GB/day** uncompressed (index-F&O 0.19 GB of it).
Conclusion unchanged — no optimisation warranted.

567 backend tests, 134 frontend tests, 71 responsive/layout/Axe guards; ruff, tsc, eslint
clean. Nothing deployed.

---

## 2026-08-07 — Session-aware capture pipeline + consolidated index-F&O domain

Implemented the live-pipeline architectural evolution plan in its own prescribed order
(§29). 566 backend tests, 134 frontend tests, 71 responsive/layout/Axe e2e guards; ruff,
tsc and eslint clean. Nothing deployed.

**Two of the plan's premises were wrong, and the code says so:**

- It asks for F&O on "all six supported indices". Only **four** are supported —
  `INDEX_CONFIGS` has NIFTY, BANKNIFTY, FINNIFTY, SENSEX, and MIDCPNIFTY/BANKEX are
  *deliberately* excluded by decision #9 in [[decisions-and-open-questions]].
  `get_index_config()` raises for them. I built the new domain generically over
  `settings.indices` rather than overriding that decision unilaterally: adding either index
  later is an `INDEX_CONFIGS` entry plus an env change, with no change to the domain.
- Index **options** were already captured; index **futures** were never subscribed at all.
  `stocks.board.build_board` collects every NFO `FUT` row and then discards the index ones
  for want of an NSE `EQ` row. So "add index F&O" really meant "keep what we were throwing
  away". Verified from the live instrument dumps: 3 futures expiries exist for all six
  indices, MIDCPNIFTY spot is token 288009, and BANKEX's BSE spot token is *not* in any
  dump we currently fetch.

**Sessions decide when persistence is valid** (`app/ops/sessions.py`). `MarketSession` +
`SessionRegistry` map each artifact onto a session; three questions of deliberately
different strictness fall out — the phase, whether a frame is *owed*, and whether absent
data is a *fault*. Every session time inherits `MARKET_OPEN`/`MARKET_CLOSE` when unset, so
the deployment's schedule is unchanged until the new env block is filled in.

**Feed health is three signals, not one** (`app/capture/feed_health.py`): transport
(packets arriving), artifact (a dataset receiving relevant updates), content (values
changing), classified into HEALTHY/QUIET/ARTIFACT_STALE/TRANSPORT_STALE/RECOVERY_*. This
decides whether the process restarts: a dead transport does, one frozen dataset does not,
and a quiet market is not a fault at all. Two latent bugs surfaced while testing it — a
quiet market reported an imminent restart that would never come, and one frozen artifact
could have restarted capture for every healthy one. Both are now impossible: the restart
spell only accumulates on feed-*wide* staleness.

**Loss accounting no longer depends on uptime.** The expected grid comes from the session
schedule and the trading date alone, so an outage appears in its own loss figure instead of
erasing itself. `app/ops/completeness.py` reconciles the schedule against frame timestamps
from the archive — telemetry only attributes causes, because a crash or power cut destroys
telemetry exactly when it matters most. Reported at two levels: total missing, then
stale/downtime/write-path/unclassified, which reconcile.

**One new dataset, no existing contract touched.** `INDEX_FNO/<date>.bin` holds every
configured index's futures *and* spot on the shared 1 Hz grid, so the basis is computable
inside one frame at one timestamp with no cross-file join. Raw state only.

**Subscription capacity is measured, not remembered** (`app/capture/subscription.py`):
1,628 tokens against a 2,700 safe threshold, one connection, headroom reported. Sharding is
planned but deliberately unwired until the numbers demand it.

**Snapshot consistency was verified, not assumed** (§18): broker callbacks never touch a
table, and tick-apply and snapshot are both non-yielding sync calls on one event loop, so
no lock is needed. Documented, including why that property is load-bearing for
cross-instrument analysis.

**Profiled before optimising** (`python -m tools.profile_capture`): the 1 Hz path uses
**0.18 ms of its 1000 ms budget (0.02%)** and tick application 2 ms per second of flow. The
new domain adds 0.06 ms/second and 0.13 GB/day. The per-frame model predicts 7.86 GB for
2026-08-07's frame count against 7.99 GB actually on disk — within ~2%. **No optimisation
is warranted**; Python is three orders of magnitude from its limit.

**Next**
- Deploy, then verify at Monday's open (2026-08-10 — the market is closed over the
  weekend, so this is the first chance to see the session gating against real ticks).
  Watch for: no `invalidated-*` files, `INDICES_FnO/` appearing under both roots,
  six artifact rows on the dashboard, PRE_OPEN 09:10→09:15 with nothing owed, and loss
  accounting starting at 09:15.
- Set `INDICES=NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX,BANKEX` and the session block in
  the deployed `.env` (the repo `.env.example` now carries the full intended values).

**Blockers**
- None. All changes are local.

---

## 2026-08-07 — Root-caused the random feed death; recovery is now restart-first

**Diagnosed** (from the deployment's own artifacts — `docker logs` for the incident days
were already gone, the container having been recreated at 16:31 IST with
`RestartCount=0`, so the evidence came from `_state/`):

- `_state/session-<date>.invalidated-<ms>.json` files are an exact record of every token
  invalidation: 5 on 08-04, 18 on 08-05, **27 on 08-06**, spaced precisely on the old
  `ReconnectPolicy` backoff curve (5, 10, 20, 40, 80, 160, 300, 300…).
- `_state/stats/capture-<date>.json` shows `token_refreshes = 0`,
  `reconnect_cycles = 0`, `exhausted = False` and `last_token_refresh_ms = None` on
  **every recorded day**, against `stale_seconds` of 542 / 4,299 / 5,445 on 08-04/05/06.
- 08-06 timeline, reconstructed to the second: capture starts 09:10:07, stale declared
  09:10:12, tier-2 attempts at 09:10:27, :47, 09:11:27, 09:12:48, 09:15:28, then every
  300 s until 10:25:32, where a single fresh tick reset the ladder, which then climbed
  again until 10:40:49. `stale_seconds = 5445` matches 09:10:12 → 10:41 exactly.
- `session_loss_pct = 0.31%` on the same day proves the write path was healthy: all
  23.4% of "data loss" was suppressed stale seconds, correctly not fabricated.

Three independent faults, none of them the broker's:

1. **`refresh_broker_session` invalidated the token before fetching a replacement.**
   calspread answered unauthenticated every time, so each attempt destroyed that day's
   session file for zero benefit — and a destroyed session means capture cannot restart.
2. **A flicker disarmed escalation.** One fresh second reset the backoff cycle, so the
   circuit breaker never tripped, `exhausted` never set, and the process never restarted
   itself during a 91-minute outage.
3. **Staleness fired before the exchange opened.** Capture starts at `MARKET_OPEN=09:10`
   but NSE trades from 09:15, so the destructive ladder ran at every open; it was
   harmless only on days when ticks happened to arrive immediately.

**Done**

- Replaced the tiered in-process ladder with **restart-first** recovery: one continuous
  stale spell, immune to flickers (ends only after 15 s of sustained freshness), escalates
  past 60 s *while trading* to `CaptureStalledError` → SIGTERM → clean container restart.
  Never restarts a feed that is healthy right now.
- Gated escalation on market phase plus a 300 s grace after `MARKET_OPEN`, without which
  restart-first would have exited the process every minute of every pre-open.
- Made `refresh_broker_session` **fetch-then-swap**: it only replaces the persisted
  session once a validated, *different* token is in hand, and takes the session lock with
  a timeout so recovery never queues behind a login.
- Added a per-trading-date escalation ledger (`stats_dir`) capping restarts at 3, after
  which the process stays up and reports `recovery_abandoned` rather than thrashing.
- Deleted `reconnect_with_refresh` and all token-provider plumbing from the live path;
  `reconnect()` remains for the documented drill.
- Telemetry: `stale_spell_seconds`, `longest_stale_spell_seconds`, `escalations`,
  `recovery_abandoned`, `recovery_armed` on the snapshot, session summary and `/health`
  (503 only once recovery is abandoned); dashboard banner distinguishes pre-open silence
  from a dying feed; operator events announce each restart escalation.
- Retired `CAPTURE_RECONNECT_*` / `CAPTURE_TOKEN_MAX_AGE_SECONDS`; added
  `CAPTURE_STALE_EXIT_SECONDS=60`, `CAPTURE_STALE_EXIT_MAX_RESTARTS=3`,
  `CAPTURE_STALE_RECOVERY_CONFIRM_SECONDS=15`, `CAPTURE_RECOVERY_ARM_DELAY_SECONDS=300`.

**Verification** — 458 backend tests, 124 frontend tests, 71 responsive/layout/a11y e2e
checks, ruff + tsc + eslint all clean.

**Next**
- Deploy and watch tomorrow's open: expect zero `invalidated-*` files, and any dead feed
  bounded to ~60 s plus one restart instead of 91 minutes.
- Run the reconnect drill in [[live-data-pipeline]] to settle whether in-process
  reconnect works at all under kiteconnect 5.x's global twisted reactor.

**Blockers**
- None. Change is local; nothing deployed to the VPS.

---

## 2026-07-21 — Unattended VPS recovery workflow completed

- Added typed Kite auth-failure propagation across REST bootstrap and ticker callbacks.
  Capture now flushes safely, invalidates the exact rejected persisted token, and lets
  the market-hours scheduler fetch, validate, and use a fresh broker token.
- Non-auth capture/writer failures remain sticky and cannot be mistaken for expiry.
- Added `MARKET_HOLIDAYS` env parsing and applied holidays to login/session,
  automation, bootstrap, and CLI calendars.
- Added `deploy/market-data-dwndr.service.example` for boot startup after Tailscale,
  Docker, network readiness, and storage mounts.
- Added focused regression coverage for callback threading, session invalidation,
  holiday suppression, bootstrap auth failures, and recoverable controller restart.

## 2026-07-21 — Operator-auth layer removed

- Removed the backend operator middleware/routes/settings and frontend unlock gate.
- HTTP console APIs now work directly on the private VPS network; WebSockets still
  enforce the `FRONTEND_URL` Origin allow-list.
- No Kite login, token-broker, daily automation, capture, or downloader behavior was
  changed by removing operator auth.
- Removed manual capture Start/Stop APIs and UI while retaining scheduler/maintenance
  controller methods. `/login` now polls automatic token fetch/validation progress.
- Added `/api/capture/history` and a monitor history table for cumulative and per-session
  live/archive bytes, file counts, index sets, and stock captures.

## 2026-07-21 — Frontend port fully env-file-driven

- Removed the last hardcoded port: the `dev`/`start` scripts now load `.env.local` via
  `dotenv-cli` (`dotenv -e .env.local -- next …`) so the serving port comes from
  **`PORT` in `.env.local`** (Next ignores `PORT` from env files on its own — verified).
  No `${PORT:-3000}` literal remains.
- Documented that `EADDRINUSE :::3000` is a port-already-in-use condition (free the port
  or change `PORT` in `.env.local`). Updated `frontend/.env.local.example` + README.
- `next build` + `eslint` clean; `npm audit` 0 vulnerabilities.

---

## 2026-07-21 — Env-only ports + CORS + indices parse fix

**Fixed**
- **`INDICES` parse failure** — pydantic-settings JSON-decoded the `list[str]` field
  before the split validator ran, so `INDICES=NIFTY,…` raised and `get_settings()`
  failed (session service never initialised → `/api/auth/login-url` 503). Annotated the
  field with `NoDecode` so the comma value reaches the validator.
- **Frontend "cannot connect"** — added `CORSMiddleware` driven by `FRONTEND_URL`
  ([[config-and-env]]); the frontend now reads the backend origin only from
  `NEXT_PUBLIC_BACKEND_URL` (no hardcoded `:8000` fallback) and derives both HTTP + WS
  URLs from it, so WS topics connect to the right port.

**Env-only ports (no hardcoded/default ports anywhere)**
- `HTTP_PORT` is now **required** (removed the `8000` default); added `HTTP_HOST` and
  `FRONTEND_URL`. New `md-serve` launcher (`app/server.py`) runs uvicorn on the env port.
- Frontend port stays env-driven via `PORT` (npm scripts); backend URL via
  `NEXT_PUBLIC_BACKEND_URL`.
- Updated `backend/.env.example` + `frontend/.env.local.example` + docs.

**Verified** end-to-end (subprocess smoke): indices parse, `login-url` 200, CORS
`Access-Control-Allow-Origin` for the frontend origin, and `/ws/{session,capture-status,
market-data}` connect + receive the welcome. 185 backend tests green, ruff clean;
`next build` + `eslint` clean.

---

## 2026-07-21 — Live capture bootstrap (end-to-end runnable)

**Done** (on `ai-dev/made`, pushed batch-by-batch)
- `kite/quotes.py` — one-shot LTP quote (static-IP client) to seed the ATM at bootstrap.
- `capture/bootstrap.py` — `bootstrap_capture()` wires instruments → index chains
  (ATM ± 50, spot-seeded) + F&O board → `IndexTable`/`StockMatrix` → writer threads →
  `CaptureEngine` + `CaptureMonitor` + optional `Broadcaster` → `TickerBridge` (all
  tokens); `run_capture()` drives the live loop. Bad indices are skipped, not fatal.
- `capture/run.py` — **`md-capture`** CLI: resume session → bootstrap → run until
  Ctrl-C (keeps raw for resume) or market close (then EOD-compresses).
- `api/capture.py` — `CaptureController` + `/api/capture/{status,start,stop}`; runs
  capture in-process so the frontend receives live broadcasts. Wired into `main.py`.
- Frontend — capture Start/Stop control on `/monitor` (`CaptureControl` + api client).
- 178 pytest tests green, ruff clean; `next build` + `eslint` clean.

**Follow-ups**
- Live end-to-end against real Kite credentials + whitelisted static IP.

---

## 2026-07-21 — Auth wiring + professionalized vault

**Done** (on `ai-dev/made`, pushed)
- **`/api/auth`** routes (`app/api/auth.py`) + **`SessionService`** (`app/session_service.py`):
  `GET /api/auth/status`, `POST /api/auth/login` (TOTP or browser `request_token`),
  `GET /api/auth/login-url`; degrade gracefully when the backend is unconfigured.
- **Startup resume** — `main.py` lifespan builds the session service and logs whether
  today's Kite session already exists (no forced re-login on mid-day restart).
- **Frontend login UX** — `lib/api.ts`, `SessionBadge` in the nav, and an `/login` page
  (TOTP + risk-free-rate form with a browser-OAuth fallback).
- **Vault** — rewrote `repo-map/` to professional standard: dashboard [[Home]], new
  [[Code-Map]] (docs ↔ source) and [[Build-Status]] dashboard, enriched every area MOC
  with status + "implemented in" code pointers, refreshed [[vault-guide]] and [[Tags]].
- Backend **159 tests green, ruff clean**; frontend `next build` + `eslint` clean.

**Follow-ups**
- Live end-to-end against real Kite credentials + whitelisted static IP.
- Optional: docker-compose for backend + frontend.

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
  header risk-free rate; time-to-expiry from `expiry_date`; `change = ltp − ohlc_close`.
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
  holding `access_token` + risk-free rate.
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
  cadence, indices L1 / stocks L5, risk-free rate in header, Greeks reconstructed on read.
- Authored the knowledge base (17 domain notes) and reorganized it into an Obsidian
  vault: `docs/` (domain folders) + `logs/` + `repo-map/` (MOCs).

**Next**
- Phase 1 build: `bin_codec` writer + reader with round-trip tests ([[implementation-plan]]).

**Blockers**
- None. All blocking decisions are resolved ([[decisions-and-open-questions]]).
