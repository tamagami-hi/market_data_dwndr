# market_data_dwndr — backend

Python/FastAPI backend for the Zerodha Kite market-data downloader (capture only, no
trading). See the knowledge base under [`../docs`](../docs) and the phase/batch plan in
[`../docs/00-overview/build-guide.md`](../docs/00-overview/build-guide.md).

## Setup

```bash
cd backend
uv venv                      # or: python -m venv .venv
uv pip install -e ".[dev]"   # runtime + dev deps
cp .env.example .env         # then fill in KITE_API_KEY / SECRET / MARKET_DATA_PATH
```

## Login (automated, TOTP from terminal)

Kite creds are seeded from `.env` (`KITE_USER_ID`, `KITE_PASSWORD`, optional
`KITE_TOTP_SECRET`). Run the headless login once per day to obtain and persist the
`access_token`:

```bash
md-login                    # prompts for TOTP (and bond yield if RISK_FREE_RATE unset)
md-login --rate 0.0691      # or: python -m app.kite.login
```

Outbound Kite calls bind `KITE_STATIC_IP` / use `KITE_HTTP_PROXY` when set, to satisfy
Kite's static-IP whitelist (Apr 2026). See `docs/60-operations/config-and-env.md`.

## Run

```bash
uvicorn app.main:app --reload --port 8000
# GET http://localhost:8000/health -> {"status": "ok", ...}
```

## Test

```bash
pytest
```

## Layout

```
app/
  main.py          FastAPI app + /health + /monitor + WS hub
  config.py        pydantic-settings (.env)
  session.py       daily session-state (access_token + bond yield)
  logging_config.py
  bin_codec/       BIN layout/writer/reader/compress (Phase 1)  <- foundation
  kite/            auth, instruments, ticks, ticker bridge (Phase 2/3)
  chain/           option-chain config/filter/assembler/table (Phase 2/3)
  stocks/          F&O board discovery + L5 matrix (Phase 2/3)
  capture/         1 Hz engine, writer threads, reconnect, monitor (Phase 3/4)
  ws/              tagged-envelope protocol + topic routes (Phase 4)
  ops/             calendar, scheduler, EOD sweep, session mgr, retention (Phase 5/7)
  historical/      intervals/windows/limiter/client/assembly/jobs (Phase 6)
  reconstruct/     Black-Scholes Greeks/IV, chain metrics, CalSpread spreads (Phase 7)
  static/          self-contained Capture Monitor dashboard (/monitor)
```

131 unit/integration tests (`pytest`), all green; `ruff` clean. Live Kite WS/REST paths
are covered with mocks/fixtures + a synthetic tick stream (no credentials needed to run
the suite).
