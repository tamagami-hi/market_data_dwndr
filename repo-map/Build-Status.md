---
title: Build-Status
area: map
type: dashboard
status: living
tags: [area/map, type/dashboard, area/build]
up: "[[Home]]"
related: ["[[build-guide]]", "[[Code-Map]]", "[[progress-log]]"]
---

# 📊 Build Status

> [!success] Overall — complete
> Phases 0–7 implemented + tested; Next.js frontend built; automated login wired.
> **Backend: 159 tests passing, ruff clean.** **Frontend: `next build` + `eslint` clean.**
> The only unexercised paths need live Kite credentials (WS/REST); those are covered by
> mocks/fixtures + a synthetic tick stream.

## Phases

| Phase | Scope | Status | Key modules |
|------:|-------|:------:|-------------|
| 0 | Scaffold (FastAPI, config, `/health`) | ✅ | `main.py`, `config.py` |
| 1 | **BIN codec** (foundation) | ✅ | `bin_codec/*` |
| 2 | Kite integration + discovery | ✅ | `kite/{auth,instruments}`, `chain/*`, `stocks/board` |
| 3 | Live capture (1 Hz) | ✅ | `kite/{ticks,ticker}`, `chain/table`, `stocks/matrix`, `capture/*` |
| 4 | Capture Monitor + WS | ✅ | `ws/*`, `capture/{monitor,broadcaster}`, `frontend/` |
| 5 | EOD compression + rollover + session | ✅ | `ops/{calendar,scheduler,eod,session_manager}` |
| 6 | Historical downloader | ✅ | `historical/*` |
| 7 | Reconstruction + hardening | ✅ | `reconstruct/*`, `ops/retention`, `logging_config` |

## Follow-on work (post-plan)

| Item | Status | Notes |
|------|:------:|-------|
| algo_engine cross-verification | ✅ | parity confirmed; 3 BS gaps fixed → [[change-log]] |
| Next.js frontend (`/monitor`, `/option-chain`, `/stocks`, `/login`) | ✅ | ported from `algo_engine/frontend_stack` |
| WS broadcaster (Greeks-enriched) | ✅ | `capture/broadcaster.py` |
| Automated login (env creds + terminal TOTP) | ✅ | `kite/login.py` (`md-login`) |
| `/api/auth` + startup resume + login UI | ✅ | `api/auth.py`, `session_service.py`, `frontend/app/login` |
| Static-IP egress for Kite (Apr 2026) | ✅ | `KITE_STATIC_IP` / `KITE_HTTP_PROXY` |
| Live capture bootstrap (`md-capture` + `/api/capture`) | ✅ | login→instruments→chains+board→ticker→1 Hz engine, broadcasting |
| Live end-to-end against real Kite creds | ⏳ | needs credentials + whitelisted IP |
| Docker-compose (backend + frontend) | 💡 | optional deploy convenience |

## Definition-of-Done gates (met)

- [x] `.bin` round-trips losslessly and re-indexes after zstd → `test_roundtrip`, `test_compress`
- [x] Byte-level header check → `test_writer`
- [x] Live L1/L5 apply → snapshot → file grows → replay → `test_capture`
- [x] Greeks match reference within tolerance; IV round-trips → `test_reconstruct_bs`
- [x] Historical job resumes without duplicate rows → `test_historical_jobs`
- [x] Automated login flow (mocked) + `/api/auth` → `test_login`, `test_auth_api`

_Run:_ `cd backend && pytest` · `cd frontend && npm run build && npm run lint`
