# market_data_dwndr — backend

Python/FastAPI backend for the Zerodha Kite market-data downloader (capture only, no
trading). See the knowledge base under [`../docs`](../docs) and the phase/batch plan in
[`../docs/00-overview/build-guide.md`](../docs/00-overview/build-guide.md).

## Setup

```bash
cd backend
uv venv                      # or: python -m venv .venv
uv sync --extra dev           # locked runtime + dev dependencies
cp .env.example .env         # then fill in credentials and both data paths
```

## Login (shared token first, automated fallback)

When `KITE_TOKEN_BROKER_URL` and `KITE_TOKEN_BROKER_PASSCODE` are configured, login first
checks the backend-only VPS endpoint for an existing session. A valid token skips TOTP
and asks only for the daily risk-free rate. An explicit unauthenticated response falls
back to `.env` credentials (`KITE_USER_ID`, `KITE_PASSWORD`), then asks for TOTP and rate.
Before proceeding, the backend validates the broker token against Kite using this API
key and the configured user id.

```bash
md-login                    # shared token → rate, or fallback TOTP → rate
md-login --rate 0.0691      # or: python -m app.kite.login
```

Outbound Kite calls bind `KITE_STATIC_IP` / use `KITE_HTTP_PROXY` when set, to satisfy
Kite's static-IP whitelist (Apr 2026). See `docs/60-operations/config-and-env.md`.

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

Two ways to run the 1 Hz capture (both: login → fetch instruments → seed ATM via LTP →
discover F&O board + index chains → subscribe → snapshot to `.bin`):

```bash
# headless (writes .bin files; auto-stops + compresses at market close):
md-capture                    # or: python -m app.capture.run
md-capture --ignore-market-hours   # run off-hours (no auto-stop), Ctrl-C to end

# in-process (so the frontend gets live WS broadcasts): from the running API,
curl -X POST "http://<HTTP_HOST>:<HTTP_PORT>/api/capture/start"
curl       "http://<HTTP_HOST>:<HTTP_PORT>/api/capture/status"
curl -X POST "http://<HTTP_HOST>:<HTTP_PORT>/api/capture/stop"
```

Both require a logged-in session (`md-login`) for the day.

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

The unit/integration suite (`pytest`) and `ruff` checks cover the login and capture paths. Live Kite WS/REST paths
are covered with mocks/fixtures + a synthetic tick stream (no credentials needed to run
the suite).
