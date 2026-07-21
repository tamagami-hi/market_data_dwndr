---
title: Build Guide (Phase / Batch)
area: build
type: plan
status: locked
tags: [area/build, type/plan, status/locked]
up: "[[Overview-MOC]]"
related: ["[[implementation-plan]]", "[[bin-structure-spec]]", "[[live-data-pipeline]]", "[[testing-strategy]]", "[[operations-runbook]]", "[[decisions-and-open-questions]]"]
---

# Build Guide (Phase / Batch)

The actionable, checkable build plan. [[implementation-plan]] is the *architecture*;
this is the *execution checklist*. Build phases in order — each depends on the prior.

## How to use this guide

- Each **Phase** is roughly one working session. It has a **Goal**, **Depends on**,
  **Batches** (task checklists), **Deliverables** (files), and a **Definition of Done**
  (acceptance criteria) that must pass before moving on.
- At the start of a session, read the phase here + its linked spec notes.
- At the end, tick the boxes and append an entry to [[progress-log]] (and
  [[change-log]] if a decision changed).
- "DoD" gates are hard — do not advance with a red gate.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done.

---

## Phase 0 — Scaffold + vault
**Goal:** runnable skeletons + this knowledge base under version control.
**Depends on:** nothing.

Batches:
- [x] Backend skeleton: `backend/pyproject.toml`, `app/main.py` (FastAPI + `/health`), `app/config.py` (pydantic-settings).
- [x] `.env.example` + `.gitignore` (`.env`, `MARKET_DATA/`, `.venv`, `node_modules/`, `__pycache__/`). See [[config-and-env]].
- [ ] Frontend skeleton: Next.js app (trimmed from `algo_engine/frontend_stack`). *(deferred to Phase 4 per handoff — "frontend can wait")*
- [x] Vault present: `docs/` + `logs/` + `repo-map/` (this repo).

**Deliverables:** `backend/`, `frontend/` skeletons, `.env.example`, `.gitignore`.
**DoD:** `uvicorn app.main:app` serves `/health`; `next dev` builds; `.env` is gitignored.

---

## Phase 1 — BIN codec  ⭐ foundation
**Goal:** exact, lossless read/write of the [[bin-structure-spec]] format.
**Depends on:** Phase 0.

Batches:
- [x] `bin_codec/layout.py` — single source of truth for field order + dtypes (`i64` paise, `u64`, `u32`), enum tags, primitives.
- [x] `bin_codec/writer.py` — frame framing `[u32 LE len][payload]`, header-once, `IndexHeader`/`IndexFrame` and `StockHeader`/`StockFrame`; NumPy `tobytes` columns.
- [x] `bin_codec/reader.py` — scan → `timestamp → (offset,size)` index; nearest-ts binary search; random-access ranges; paise→rupees on read.
- [x] `bin_codec/compress.py` — whole-file zstd L17 → `.bin.zst`; transparent read of `.zst`.

**Deliverables:** `backend/app/bin_codec/{layout,writer,reader,compress}.py` + tests.
**DoD** (see [[testing-strategy]]):
- Round-trip: write index + stock frames → read back → **identical integer arrays**.
- Byte-level: a hand-built header parses to the expected field values.
- Compress `.bin` → `.bin.zst` → re-index → identical frame timestamps/values.

---

## Phase 2 — Kite integration + discovery
**Goal:** authenticate, get instruments, build per-index chains + the stock board.
**Depends on:** Phase 1.

Batches:
- [ ] `kite/auth.py` — `.env` api_key/secret; login-URL → `request_token` → `access_token`; capture the day's **bond yield**; persist to session state ([[session-state]]).
- [ ] `kite/instruments.py` — fetch instrument dump per exchange; cache + **daily archive** to `_instruments/` ([[storage-layout]]).
- [ ] `chain/filter.py` + `chain/assembler.py` — `get_spot_atm(step)`, `option_chain_filter`, per-index table + token map ([[option-chain-selection]]).
- [ ] `stocks/board.py` — CalSpread discovery (spot + 3 nearest futures) ([[stocks-capture]]).

**Deliverables:** auth, instruments, chain assembler, stock board modules + tests.
**DoD:**
- Login yields a usable `access_token`; bond yield stored in session state.
- For each index (NIFTY/BANKNIFTY/FINNIFTY/SENSEX) the filter returns exactly the ATM ± 50 window on real instrument data.
- Board lists ~all F&O stocks, each with spot + up to 3 futures.

---

## Phase 3 — Live capture
**Goal:** subscribe, apply ticks, write 1 Hz frames to `.bin`.
**Depends on:** Phases 1–2.

Batches:
- [ ] `kite/ticker.py` — KiteTicker → `asyncio.Queue` bridge; `full` mode subscribe (~1,600 tokens, one connection).
- [ ] `chain/table.py` + `stocks/matrix.py` — NumPy integer tables (index L1) and stock matrix (L5); O(1) token→index apply; unmatched counter.
- [ ] `capture/engine.py` — 1 Hz snapshot loop; per-file writer threads; reconnect/backoff + stall detection ([[live-data-pipeline]]).

**Deliverables:** ticker bridge, tables/matrix, capture engine + writer wiring.
**DoD:**
- Against a live/replayed feed, `.bin` files grow ~1 frame/s per file; reader replays them.
- Reconnect recovers subscriptions without data corruption.
- Index depth = L1, stock depth = L5 verified in the written frames.

---

## Phase 4 — Interactive frontend (Capture Monitor)
**Goal:** visualize what is being saved + reused option-chain/stock views.
**Depends on:** Phase 3.

Batches:
- [ ] `ws/protocol.py` + `ws/routes.py` — tagged envelope; topics `market-data`, `stocks`, `capture-status`, `session`, `historical-jobs` ([[websocket-protocol]]).
- [ ] `capture/monitor.py` — per-underlying + global `CaptureStatus` metrics.
- [ ] Frontend `/monitor` dashboard + reused `/option-chain`, `/stocks` ([[frontend]]).

**Deliverables:** WS protocol/routes, monitor metrics, Capture Monitor page.
**DoD:** dashboard shows per-underlying WS health, frames-written, file size, 1 Hz heartbeat, disk usage — updating live.

---

## Phase 5 — EOD compression + rollover + session-state
**Goal:** clean daily lifecycle.
**Depends on:** Phases 1, 3.

Batches:
- [ ] Market-hours scheduler + trading-calendar handling ([[operations-runbook]]).
- [ ] EOD: flush, close files, zstd L17 sweep, rotate to next day.
- [ ] Session-state persistence + mid-day restart/resume ([[session-state]], [[failure-modes]]).

**Deliverables:** scheduler, EOD sweep, session-state module.
**DoD:** at close, raw `.bin` → `.bin.zst` (raw removed); a mid-day restart resumes with the same access_token + bond yield and appends to today's files.

---

## Phase 6 — Historical downloader
**Goal:** backfill candles into the same `.bin` format.
**Depends on:** Phases 1–2.

Batches:
- [ ] `historical/` — REST fetch, window chunking, token-bucket limiter, retries, request validation ([[historical-data]]).
- [ ] Frame assembly (`bin_export` pattern) → `INDICES_HIS/` & `STOCKS_HIS/`.
- [ ] Resume via `_state/` checkpoints; `historical-jobs` progress on WS.

**Deliverables:** historical jobs, windows, limiter, storage + UI wiring.
**DoD:** a job downloads a date range, writes valid `.bin`, and **resumes** from a mid-run checkpoint without duplicate rows.

---

## Phase 7 — Reconstruction + hardening
**Goal:** derive Greeks/metrics on read; production polish.
**Depends on:** all above.

Batches:
- [ ] `reconstruction` — Greeks/IV on read (Black-Scholes + header bond yield); CalSpread spread/summary rebuild ([[reconstruction]]).
- [ ] Failure-mode handling + data-retention ([[failure-modes]], [[data-retention]]).
- [ ] Full test pass, logging/metrics, docs finalize ([[testing-strategy]]).

**Deliverables:** reconstruction module, hardening, tests, final docs.
**DoD:** Greeks reconstructed from a stored `.bin` match a reference within tolerance; retention/cleanup runs; test suite green.

---

## Cross-phase acceptance (project done)

- [ ] `.bin` round-trips losslessly (integers) and re-indexes after zstd.
- [ ] Live capture writes indices (L1) + stocks (L5) at 1 Hz across a full session.
- [ ] Capture Monitor reflects reality (files, sizes, health).
- [ ] Historical backfill resumes cleanly.
- [ ] Greeks/spreads reconstructable on read from stored raw + bond yield.
- [ ] Ops: morning start, EOD compression, restart/resume all documented and working
  ([[operations-runbook]]).
