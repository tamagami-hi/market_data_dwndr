# market_data_dwndr — backend

Python/FastAPI backend for the Zerodha Kite market-data downloader (capture only, no
trading). See the knowledge base under [`../docs`](../docs) and the phase/batch plan in
[`../docs/00-overview/build-guide.md`](../docs/00-overview/build-guide.md).

## Setup

```bash
cd backend
uv venv                      # or: python -m venv .venv
uv sync --extra dev           # locked runtime + dev dependencies
cp .env.example .env         # then fill in Calspread/session-source config and data paths
```

## Calspread session synchronization and daily initialization

With `KITE_TOKEN_BROKER_URL` and `KITE_TOKEN_BROKER_PASSCODE` configured, the lifespan
scheduler retrieves and validates Calspread's existing token during the configured
trading-day window. The frontend `/login` page is a read-only initialization view: it
shows broker configuration, fetch/validation state, capture readiness, and whether the
downloader is running. It does not start login or capture.

Calspread is the target architecture's sole Kite login/session authority. Repeating a lookup
normally returns the same access token until an administrator relogs in through Calspread.
Backend, WS, REST, and container restarts reuse that token; feed recovery never requests a
different one. The risk-free rate is fetched from the Calspread rate endpoint (env fallback).

The commands below describe a legacy direct-login implementation that still exists in code.
They are not the target operational fallback and must not be copied into distributed services.
When Calspread has no session or Kite rejects its current token, the target operation is for an
administrator to relogin through Calspread.

Run it interactively inside the backend container (needs a TTY for the hidden prompts):

```bash
docker exec -it <backend-container> md-login            # seeded creds + TOTP
docker exec -it <backend-container> md-login --manual   # enter all 4 creds + TOTP
```

- default (seeded): uses the `.env` credentials (`KITE_USER_ID`, `KITE_PASSWORD`,
  `KITE_API_KEY`, `KITE_API_SECRET`) and prompts only for the TOTP.
- `--manual`: prompts for all four credentials on the terminal (nothing is written back
  to the env), then the TOTP.
- `--force`: log in even if a valid session for today already exists.

Broker tokens are validated by the exchange step before persistence. Credentials are
read via `getpass` (no echo, never in shell history or argv).

See [`broker-session-authentication.md`](../docs/60-operations/broker-session-authentication.md)
for the canonical target design and its `AUTH_BLOCKED` recovery state.


This service only downloads market data, so it needs no static egress IP: Kite's
static-IP whitelist (Apr 2026) applies only to order-placement endpoints (handled by the
separate `algo_engine`). See `docs/60-operations/config-and-env.md`.

## Run

The bind host + port come **only** from `.env` (`HTTP_HOST`, `HTTP_PORT`) — there is no
hardcoded/fallback port. Start with `md-serve` so the env port is used:

```bash
md-serve                     # or: python -m app.server  (reads HTTP_PORT from .env)
# GET http://<host>:<HTTP_PORT>/health -> {"status": "ok", ...}
```

`FRONTEND_URL` in `.env` configures CORS (the browser origin[s] allowed to call the API);
it must be the URL serving the frontend page. `NEXT_PUBLIC_BACKEND_URL` is different:
it is the browser-reachable backend/API origin.
The default bind is loopback-only because login endpoints use server-side credentials.
Use TLS and API authentication before changing `HTTP_HOST` to a network-facing address.

## Capture

The FastAPI lifespan owns the production 1 Hz capture through `DailyAutomationService`:
it retrieves or reuses a validated Calspread session, starts capture inside the configured market window,
stops and flushes at close, and runs EOD archiving. The HTTP surface is intentionally
read-only for normal operation:

```bash
curl "http://<HTTP_HOST>:<HTTP_PORT>/api/capture/status"
curl "http://<HTTP_HOST>:<HTTP_PORT>/api/capture/history"
```

The history endpoint reports cumulative and per-session raw/archive bytes and captured
index/stock file counts. Browser Start/Stop endpoints are not exposed; internal
`CaptureController.start()` / `stop()` remain owned by the scheduler and release drain.

`md-capture` remains available as a separate operational CLI when explicitly needed:

```bash
md-capture
md-capture --ignore-market-hours
```

Production capture requires a validated Calspread session, normally retrieved automatically
from the configured endpoint. In the target architecture, an unavailable or rejected session
requires administrator login through Calspread; `md-login` is legacy implementation drift, not
the fallback design.

At EOD, raw files remain under `MARKET_DATA_PATH` until a `.bin.zst` has been written,
stream-verified, and atomically published under `ARCHIVE_DATA_PATH`. The archive keeps
the same relative `INDICES/`, `STOCKS/`, and historical directory structure.

For the production container workflow, see
[`docs/60-operations/vps-docker-deployment.md`](../docs/60-operations/vps-docker-deployment.md).

## Test

```bash
pytest
```

## Layout

```
app/
  main.py          FastAPI app + /health + /monitor + WS hub
  config.py        pydantic-settings (.env)
  session.py       transitional combined token cache + daily risk-free-rate state
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

The unit/integration suite (`pytest`) and `ruff` checks cover the login and capture paths. Live Kite WS/REST paths
are covered with mocks/fixtures + a synthetic tick stream (no credentials needed to run
the suite).
