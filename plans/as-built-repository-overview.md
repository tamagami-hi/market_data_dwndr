---
title: As-Built Repository Overview
area: overview
type: overview
status: living
tags: [area/overview, type/overview, status/living, as-built]
up: "[[Home]]"
related: ["[[overview-and-scope]]", "[[Code-Map]]", "[[distributed-capture-architecture-plan]]"]
---

# As-Built Repository Overview — what `market_data_dwndr` actually does

This note describes the system **as it exists in the tree today**, verified by reading the
code rather than the specs. `docs/00-overview/overview-and-scope.md` states the *intent*;
this note states the *reality*, including the places where the two have drifted apart
(§10).

---

## 1. In one paragraph

`market_data_dwndr` (product name **TickVault**) is a self-hosted **market-data recorder
for Zerodha Kite**. It runs unattended for a trading day: it obtains a Kite access token
from an external session authority, discovers the day's instrument universe, subscribes to
one Kite WebSocket feed, and writes a **1 Hz snapshot of the entire market it is watching**
into compact integer-native binary files. At market close it compresses those files with
zstd, verifies the archive byte-for-byte, and publishes it to a separate archive disk. A
FastAPI service exposes read-only status/telemetry plus WebSocket topics, and a Next.js
frontend renders a live option chain, an F&O board, and an operations dashboard.

**It never trades.** There is no order placement, position, P&L, risk, or strategy code
anywhere in the repo — that lives in a separate `algo_engine` project. This repo only
captures and stores data.

---

## 2. The four datasets it records

All four are written concurrently, on the same 1-second grid, by one process.

| Dataset | Path | Contents | Depth |
|---|---|---|---|
| Index option chains | `INDICES/<INDEX>/<date>.bin` | ATM ± 50 strikes (101), calls + puts, per index, plus spot and India VIX | **L1** |
| F&O stock board | `STOCKS/<date>.bin` | Every F&O stock as one matrix: spot + 3 nearest futures per row | **L5** |
| Consolidated index F&O | `INDICES_FnO/<date>.bin` | Index futures (3 nearest expiries) + each index's spot, all indices on one grid | L1 |
| Historical candles | `INDICES_HIS/`, `STOCKS_HIS/` | OHLC + OI candles, same binary format | n/a |

Index universe default (`app/config.py:DEFAULT_INDICES`): **NIFTY, BANKNIFTY, FINNIFTY,
MIDCPNIFTY, SENSEX, BANKEX** — overridable with `INDICES=...`. Stock universe is `all` or
a comma allow-list.

Alongside the data it keeps operational state under `MARKET_DATA_PATH`:

- `_instruments/<date>/{NFO,NSE,BFO}.csv` — the day's instrument masters, archived so any
  past day's ATM window and expiry tokens can be reconstructed after those tokens expire.
- `_state/` — the daily session cache (access token + risk-free rate + trading date).
- `_state/stats/` — persisted monitor snapshots, compression history, and the per-date
  escalation ledger. These are what let a mid-session restart resume the day's counters
  instead of reporting zeros.

---

## 3. How a trading day actually runs

The FastAPI **lifespan** owns production capture; there are deliberately no browser-facing
start/stop endpoints. `app/ops/automation.py` splits this into a pure decision function
(`decide_automation`) and a dispatcher (`DailyAutomationService`, ticking every 5 s):

```
non-trading day / holiday        -> stop capture if running, do nothing else
08:30-09:00 IST (auth window)    -> FETCH_BROKER_TOKEN, at most once per 60 s
09:00-15:30 IST (capture window) -> START_CAPTURE once the session is ready
>= 15:30 IST                     -> STOP_CAPTURE, then RUN_EOD (once per trading date)
```

Times, holidays, and the poll window are all env-configured; the timezone default is
`Asia/Kolkata`.

**Session authority.** The target design is that this downloader **never creates, exchanges,
refreshes, or rotates a Kite access token**. An administrator logs in to Kite through
Calspread (`calspread.online`); the downloader retrieves that already-issued token from
`KITE_TOKEN_BROKER_URL`, validates it against the Kite profile endpoint, and caches it for
restarts. Repeated retrievals return the same token, so reconnect logic must reuse the
token and restart the *transport*. The daily risk-free rate comes from
`KITE_RATE_BROKER_URL` (env `RISK_FREE_RATE` is the fallback) and is stamped into every
file header so each file is self-contained for Greek reconstruction.

**Bootstrap** (`app/capture/bootstrap.py`) is the interesting part of a start:

```
instrument dumps ──▶ per-index option chains (ATM ± 50, seeded by one LTP quote)
                ├──▶ F&O stock board (spot + 3 nearest futures)
                └──▶ index-F&O board (index futures + spot)
     ──▶ NumPy integer tables/matrices ──▶ one writer thread per output file
     ──▶ CaptureEngine + CaptureMonitor + Broadcaster
     ──▶ TickerBridge subscribing every token on one connection
```

A failure in one domain degrades rather than aborts: a bad index is skipped, a failed board
discovery is logged, and the run continues as long as *something* is capturable. An
authentication error is the exception — it is raised so recovery can act on it.

**Restart-first recovery.** The engine tracks feed freshness and reacts on a ladder
(defaults, all env-tunable):

| Signal | Default | Effect |
|---|---|---|
| No content-changing ticks | 5 s | Feed flagged stale; **writes are suppressed** |
| Continuous staleness while trading | 60 s | Escalate — process exits so Docker restarts it clean |
| Sustained fresh ticks needed to clear a spell | 15 s | Prevents a single tick from resetting the deadline |
| Grace period after open | 300 s | Pre-open silence is not a fault |
| Escalations per trading date | 3 | Warn-only; recovery never stops while the session is open |

Suppressing stale writes is a deliberate data-integrity choice: a frozen frame would
fabricate a second of market data that never traded, whereas an honest hole in the file is
auditable after the fact. Because `.bin` files append, a restart mid-session re-opens the
same file, re-scans it to recover the frame count, and continues — no second header, no
duplicate rows. Measured restart downtime is 25–30 s (pinned by `test_restart_resume.py`).

**EOD** (`app/ops/eod.py`): stop capture, flush and fsync the writers, stream each raw
`.bin` through zstd L17, verify the archive against the source, atomically publish it under
`ARCHIVE_DATA_PATH` mirroring the same relative layout, and only then remove the raw file.
A failure on one file keeps that raw file and continues with the rest.

---

## 4. The storage format (`app/bin_codec/`)

A format this project owns, borrowing `algo_engine`'s *approach*. All little-endian:

```
file  := header_frame data_frame*
frame := u32 payload_len   payload[payload_len]
```

- The header (tag `0`) is written exactly once, when the file is empty; every later frame
  is a data frame (tag `1`).
- **Everything is a native integer.** Prices and index values are `i64` **paise**
  (₹24567.05 → `2456705`; VIX 12.34 → `1234`). Quantities, OI, volume, tokens, timestamps
  and sequence numbers are `u64`. The *only* float in the format is the header's
  `risk_free_rate` (`f64`).
- Columns are written as NumPy `tobytes()` vectors aligned to the header's strike/row
  order, so a frame is a set of parallel arrays rather than a row of structs.
- Reads scan once to build `timestamp → (offset, length)`, then support nearest-timestamp
  binary search and random-access frame ranges. A **torn trailing frame** (from a hard
  kill) is detected and ignored rather than corrupting the scan. `.bin.zst` is read
  transparently.

Rough raw sizes before compression: ~24.5 KB per index frame (~0.55 GB/index/day) and
~0.26 MB per stock frame (~5.8 GB/day for ~200 stocks).

**Stored vs reconstructed.** Only raw API fields are persisted. IV, delta, gamma, vega,
theta, rho, net change, change-in-OI, PCR, max pain, and calendar spreads are **never
written** — they are recomputed on read from the raw columns plus the header's risk-free
rate (`app/reconstruct/{bs,greeks,metrics,spreads}.py`). That keeps files smaller, keeps
them lossless with respect to the exchange, and means a fix to the Greeks model
retroactively improves every historical file.

---

## 5. Interfaces (verified against the routers)

**HTTP — read-only in normal operation.**

| Endpoint | Purpose |
|---|---|
| `GET /health` | Per-component ok/degraded/dead probe; HTTP 503 only when a component is *dead* |
| `GET /monitor` | Self-contained zero-build HTML dashboard served by FastAPI |
| `GET /api/status` | Merged session + automation + capture telemetry + last EOD sweep; `?format=text` renders a terminal table |
| `GET /api/stats` | Aggregated statistics payload |
| `GET /api/capture/status` | Live capture telemetry |
| `GET /api/capture/history` | Cumulative and per-date raw vs verified-archive bytes, file counts |
| `GET /api/capture/stocks/{symbol}/depth` | Full L5 depth for one stock |
| `POST`/`DELETE /api/capture/maintenance[/{lease_id}]` | Bounded release-drain lease (secret-header gated) |
| `GET /api/auth/status`, `GET /api/auth/login-url`, `POST /api/auth/login/*` | Session status; staged login retained from the legacy direct-login path |

There is no `POST /capture/start` or `/stop` — the scheduler and the release drain own the
capture lifecycle.

**WebSocket:** `GET /ws/{topic}` with an allow-list of `market-data`, `stocks`,
`capture-status`, `session`, `historical-jobs`. Messages are tagged envelopes
(`{type, payload}`); the option grid uses full keyframes plus sparse deltas on changed
strikes. Connections whose `Origin` is not in `FRONTEND_URL` are closed with 1008. The
index-F&O board deliberately rides the `stocks` topic so the board page needs one
connection.

**CLIs** (`pyproject.toml [project.scripts]`): `md-serve` (uvicorn with the env port),
`md-capture` (standalone capture run), `md-login` (legacy interactive direct login).

---

## 6. Frontend

Next.js 16 / React 19 / Tailwind v4 / `@tanstack/react-virtual`, no chart or icon
dependencies beyond that. Pages that actually exist:

- `/` — landing shell
- `/monitor` — the operations dashboard: per-underlying health, frames written, file sizes,
  data-loss diagnostics, frame integrity, session history, storage history, compression
  activity, alerts, connection quality
- `/option-chain` — live chain with reconstructed IV/Greeks, ATM and max-pain markers
- `/stocks` — F&O board, L5 depth panel, calendar spreads
- `/login` — **read-only** initialization view: broker configuration, token
  fetch/validation state, capture readiness. It exposes no credential or TOTP input; when
  auth is blocked it tells the administrator to relogin through Calspread.

`lib/` holds the WS topic connection with reconnect, topic hooks, and the board/chain/depth
decoders; `hooks/useMonitorTelemetry.ts` drives the dashboard.

---

## 7. Deployment and release tooling

- `compose.yaml` — two hardened services (backend, frontend): read-only root filesystem,
  `cap_drop: ALL`, `no-new-privileges`, non-root UID/GID, tmpfs for `/tmp`, healthchecks,
  and bind mounts for the two data roots with `create_host_path: false`. Every port and
  path comes from `.env` with `:?` guards — nothing is hardcoded.
- `release_manager/` — image-based release flow. `export.sh` builds both images and
  assembles a single self-contained `DATA_DOWNLOADER` bundle; that folder is the only thing
  shipped to the VPS and can deploy and roll itself back there with no git checkout, source,
  or build tools on the server. `manifest.json` records tags, image IDs, and sha256 sums
  which the VPS runner re-verifies before loading. Release ids are
  `<12-char git sha>-<12-char build-config hash>`.
- `deploy/nginx/` — vhost config for the internal domain.
- `layout-audit/` — Puppeteer screenshot/layout audit artifacts captured per version.

---

## 8. Repository map

```
backend/          Python 3.11+ FastAPI service — the whole capture system (~13.3k LOC app)
  app/bin_codec/    binary layout, writer, reader, scan, zstd compress
  app/kite/         auth, token/rate brokers, instruments, ticks, ticker bridge, quotes, login
  app/chain/        per-index config, ATM±50 filter, chain assembler, live L1 table
  app/stocks/       F&O board discovery, L5 matrix, depth
  app/index_fno/    consolidated index-futures board + matrix
  app/capture/      1 Hz engine, writer threads, reconnect, feed health, monitor,
                    broadcaster, bootstrap, snapshot, subscription planner, maintenance
  app/ops/          trading calendar, session registry, daily automation policy, EOD sweep,
                    completeness, retention, stats store
  app/historical/   intervals, window chunking, token-bucket limiter, client, assembly, jobs
  app/reconstruct/  Black-Scholes, per-frame Greeks/IV, chain metrics, calendar spreads
  app/ws/           envelope protocol + topic routes
  app/api/          auth, capture, status, stats, health routers
  app/static/       dependency-free /monitor dashboard
  tests/            54 pytest modules, ~10.9k LOC
frontend/         Next.js app (32 vitest suites, 5 Puppeteer e2e suites incl. visual + axe)
docs/             Obsidian-style knowledge base, numbered 00-overview .. 99-reference
repo-map/         MOC index notes for the docs vault (Home, Code-Map, Build-Status, ...)
plans/            forward-looking design plans (this note lives here)
logs/             progress-log.md, change-log.md, next-session-handoff.md
release_manager/  build/ship/rollback/status scripts + the shippable bundle
deploy/           nginx vhost
layout-audit/     per-version UI screenshots and layout JSON
MARKET_DATA/      local dev data root (_state only in the repo)
```

---

## 9. Verification status

- **Backend: 572 tests collected** across 54 modules (`pytest --collect-only -q`), ruff-clean
  line length 100, mypy configured. Test volume is close to app volume (10.9k vs 13.3k LOC),
  and the suite is unusually behavioural: `test_session_lifecycle_drill.py` drives a fake
  clock through an entire trading day, `test_stale_write_suppression_e2e.py` proves an
  outage leaves a hole rather than fabricated frames, `test_restart_resume.py` pins the
  restart downtime window, and `test_daily_automation.py` pins the scheduler's action order.
- **Frontend:** 32 vitest suites plus Puppeteer e2e for login flow, responsive layout,
  option layout, performance, and visual regression against stored baselines.
- **Not covered by CI:** the live Kite WebSocket and REST paths, which need real
  credentials. Those are exercised with mocks, fixtures, and a synthetic tick stream.

---

## 10. Drift: where the code and the docs disagree

Worth knowing before trusting any single document in this repo.

1. **`app/historical/` is complete but unreachable.** Intervals, window chunking, the
   token-bucket limiter, the retrying client, frame assembly, and resumable jobs are all
   implemented and tested, and `jobs.py` publishes progress to the `historical-jobs` WS
   topic. But nothing outside the package imports it — no route, no CLI entry point, no
   scheduler hook, and no `/historical` frontend page. Today it is a tested library with no
   caller.
2. **`md-login` / `kite/login.py` is acknowledged legacy drift.** Direct
   TOTP/request-token/API-secret handling still exists in code; the target design is
   Calspread-only, with administrator relogin as the recovery path. The backend README and
   the auth spec both say so explicitly.
3. **`repo-map/Code-Map.md` is stale.** It references `ops/scheduler.py` and
   `ops/session_manager.py`, which no longer exist (replaced by `ops/automation.py` and
   `ops/sessions.py`), and reports 178 tests against an actual 572.
4. **Index count.** `docs/00-overview/overview-and-scope.md` and the Phase 2 DoD name four
   indices; `config.py` defaults to six (adds MIDCPNIFTY and BANKEX).
5. **`docs/50-frontend/frontend.md` lists a `/historical` page** that is not in
   `frontend/app/`.
6. **The single 09:00–15:30 window is known to be wrong.** Per
   `plans/distributed-capture-architecture-plan.md`, the market's closing structure changed
   on 2026-08-03 and no segment matches that one window. `app/ops/sessions.py` (the session
   registry, with per-session schedules, pre-open handling, and scheduled-second
   accounting) is the in-tree path forward; every artifact currently still shares the one
   equity-derivatives session.
7. **`app/security/__init__.py` is a docstring-only placeholder** — the security boundary it
   names is currently enforced inline in the WS routes and the maintenance API.

---

## 11. Where to look next

| Question | File |
|---|---|
| Byte-level format | `docs/20-data-and-storage/bin-structure-spec.md` |
| On-disk layout and archive mirroring | `docs/20-data-and-storage/storage-layout.md` |
| Live pipeline internals | `docs/30-live-capture/live-data-pipeline.md` |
| Token/session authority and `AUTH_BLOCKED` | `docs/60-operations/broker-session-authentication.md` |
| Every env var | `docs/60-operations/config-and-env.md`, `backend/app/config.py` |
| Day-to-day operations | `docs/60-operations/operations-runbook.md` |
| VPS container workflow | `docs/60-operations/vps-docker-deployment.md` |
| Proposed service split (ZMQ tick bus) | `plans/distributed-capture-architecture-plan.md` |
| What changed and when | `logs/progress-log.md`, `logs/change-log.md` |
