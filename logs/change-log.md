---
title: Change Log
area: logs
type: log
status: living
tags: [area/logs, log/change, status/living]
up: "[[Logs-MOC]]"
related: ["[[decisions-and-open-questions]]", "[[progress-log]]"]
---

# Change Log

Design decisions and notable changes. Newest first.

---

## 2026-07-21 — Unattended token recovery and boot lifecycle completed
- Kite REST/WebSocket authentication failures now stop and flush capture, invalidate
  only the exact rejected daily session, and return automation to the existing secure
  broker polling path; replacement tokens resume capture without a backend restart.
- Added validated `MARKET_HOLIDAYS` configuration to every runtime trading calendar.
- Added a Docker Compose systemd unit ordered after Tailscale, Docker, networking, and
  required storage mounts; it starts existing release images after preflight checks.

## 2026-07-21 — Operator-auth layer removed
- Removed the separate operator token, unlock endpoint, HttpOnly browser session,
  HTTP middleware, WebSocket cookie gate, and frontend unlock screen for the private
  home-VPS deployment.
- `FRONTEND_URL` remains the CORS/WebSocket Origin allow-list; host-level loopback or
  Tailscale access is the deployment boundary. Kite login, shared-token polling,
  scheduling, capture, and release-maintenance authentication are unchanged.
- Removed browser Start/Stop capture endpoints and controls; the market-hours scheduler
  exclusively owns normal capture lifecycle. Replaced Start login with automatic broker
  fetch/validation progress, and added per-session live/archive download history.

## 2026-07-21 — Phases 2–7 implemented (full backend build)
- Built the entire capture pipeline on `ai-dev/made` per the locked specs, batch-by-batch
  with tests + ruff after each: Kite auth/instruments/discovery, chain filter/assembler,
  stock board, KiteTicker→asyncio bridge, L1/L5 tables + matrix, 1 Hz capture engine +
  writer threads + reconnect/stall policy, WS tagged-envelope protocol/routes + Capture
  Monitor, trading calendar/scheduler + EOD sweep + session resume, historical downloader
  (intervals/windows/limiter/client/assembly/jobs), and reconstruction (BS Greeks/IV,
  chain metrics, CalSpread spreads).
- No schema/format decisions changed — specs implemented as-is. Non-schema choices:
  price sentinels 0 for empty depth; VIX token fans out to all index tables; `/monitor`
  shipped as a standalone dependency-free dashboard (algo_engine Next.js components are
  not in this repo, so the reused option-chain/stocks pages are deferred).

## 2026-07-21 — Build started: Phase 0 scaffold + Phase 1 BIN codec landed
- Implemented the `backend/` FastAPI scaffold and the full integer-native BIN codec
  (`app/bin_codec/{layout,writer,reader,compress}.py`) exactly per
  [[bin-structure-spec]]. No format/schema decisions changed — the spec was
  implemented as-is (schema_version = 1).
- Implementation choices (non-schema): little-endian NumPy dtypes so `tobytes()` is the
  wire layout; raw `.bin` read via `mmap`, `.zst` via whole-stream decompress; reader
  returns raw integers (paise→rupees kept as an explicit separate step so round-trips
  stay bit-exact); verified raw removal after compression.
- Marked Phase 0 (minus deferred frontend) and Phase 1 batches done in [[build-guide]].

## 2026-07-21 — Branch setup: main (baseline/default) + ai-dev/made (working)
- Created `main` as the stable/default baseline and `ai-dev/made` as the active
  development branch. Phase work lands on `ai-dev/made` and PRs into `main`.
- Documented the git/PR workflow in [[next-session-handoff]].

## 2026-07-21 — Gap docs + build guide added; vault completed
- Added [[build-guide]] (phase/batch checklist with DoD gates), the `60-operations`
  domain ([[operations-runbook]], [[config-and-env]], [[session-state]],
  [[failure-modes]], [[data-retention]]), [[testing-strategy]], and [[reconstruction]].
- New MOCs [[Operations-MOC]] + [[Quality-MOC]]; Home/Overview/Data-Storage/Tags updated.
- Knowledge base is now complete; ready to branch + push.

## 2026-07-21 — Knowledge base reorganized into an Obsidian vault
- Moved 17 flat docs into domain folders under `docs/`; added YAML frontmatter,
  wikilinks, and tags. Created `logs/` and `repo-map/` (MOCs). See [[Home]].

## 2026-07-21 — Depth levels locked: indices L1, stocks L5
- Rationale + sources in [[depth-level-research]]. Affects [[bin-structure-spec]],
  [[stocks-capture]].

## 2026-07-21 — Numeric types → integer-native (lossless)
- Prices as `i64` paise, quantities/OI `u64`, orders `u32`; bond yield the only `f64`.
- Replaces the earlier `f64` plan. See [[lossless-and-precision]], [[bin-structure-spec]].

## 2026-07-21 — Greeks/IV not stored; bond yield stored in header
- Greeks reconstructed on read (Black-Scholes + stored 10-yr yield). Bond yield entered
  manually at login. Means files are **not** the byte-identical `algo_engine` structs.
  See [[bin-format]].

## 2026-07-21 — Format engine: struct + NumPy + zstandard
- Dropped msgpack/bincode-lib/Parquet ideas. Historical uses the same `.bin` format.
  See [[tech-stack-and-efficiency]].

## 2026-07-21 — Scope, cadence, universe
- 1 Hz capture; indices NIFTY/BANKNIFTY/FINNIFTY/SENSEX (no MIDCPNIFTY/BANKEX); full
  F&O stock board; KiteTicker; `.env` api key+secret + daily login. See
  [[decisions-and-open-questions]].


## 2026-07-21 — Cross-verified against algo_engine Rust source
- Cloned `tamagami-hi/algo_engine` and diffed the Python ports against the Rust
  reference (`oc_maker/table/{filter,assembler}.rs`, `bs_models.rs`, `utils.rs`,
  `metrics/calculations.rs`, `historical/orchestrator/bin_export.rs`,
  `stream/ingestion.rs`).
- **Confirmed parity:** ATM window + nearest-strike-on-tie, per-1% Greek normalization
  (vega/100, rho/100) + per-day theta (/365), max-pain/PCR aggregation, reconnect policy
  (5s→300s, 20 attempts, exponential), and header-once + per-date sequence in bin export.
- **Fixed 3 parity gaps** in `reconstruct/`: time-to-expiry now uses **365.25** days/yr
  (was 365) with a `1e-5` maturity floor; the IV intrinsic-value guard now uses
  algo_engine's combined tolerance `max(intrinsic·0.5%, ₹0.50)`; added a **VIX-derived
  fallback IV** when the solve fails within tolerance.
- **Intentional (kept) divergences:** `.bin` stores raw only — no IV/Greeks/change_in_oi
  columns (algo_engine persists derived Greeks); custom struct packing instead of bincode;
  per-index ATM step (50/100) instead of a hardcoded 50. All match the market_data_dwndr
  locked specs.


## 2026-07-21 — Automated Kite login (env-seeded creds + terminal TOTP)
- Added a headless Kite login (`app/kite/login.py`, `md-login` entrypoint) that
  automates algo_engine's OAuth flow: `/api/login` → `/api/twofa` (TOTP) →
  `/connect/login` redirect → `request_token` → `/session/token` exchange, persisting
  today's `access_token` + bond yield to session state.
- Credentials are **seeded from the environment** (`KITE_USER_ID`, `KITE_PASSWORD`,
  optional `KITE_TOTP_SECRET`, `RISK_FREE_RATE`) rather than a database (algo_engine
  keeps them encrypted in Postgres). The **TOTP is taken from the terminal** when no
  secret is configured; otherwise it is generated via `pyotp`.
- Outbound Kite calls go through one client that can bind a **static source IP**
  (`KITE_STATIC_IP`) or use a proxy (`KITE_HTTP_PROXY`) to meet Kite's static-IP
  whitelist requirement (Apr 2026).
- Only `backend/.env.example` is maintained in-repo (real `.env` provided at deploy).
