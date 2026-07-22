---
title: Config & .env Reference
area: operations
type: spec
status: locked
tags: [area/operations, type/spec, status/locked]
up: "[[Operations-MOC]]"
related: ["[[operations-runbook]]", "[[session-state]]", "[[implementation-plan]]", "[[storage-layout]]"]
---

# Config & `.env` Reference

Configuration is via **pydantic-settings** reading a `.env` file (typed, validated).

## `.env` variables

| Var | Required | Example | Purpose |
|---|---|---|---|
| `KITE_API_KEY` | ✅ | `abcd1234` | Kite Connect app key |
| `KITE_API_SECRET` | ✅ | `wxyz5678` | Used to exchange `request_token` → `access_token` |
| `MARKET_DATA_PATH` | ✅ | `/data/MARKET_DATA` | Output root ([[storage-layout]]) |
| `ARCHIVE_DATA_PATH` | ✅ | `/data/z_market_data` | Separate root for verified `.bin.zst` archives |
| `KITE_USER_ID` | login | `AB1234` | Zerodha login id (automated `md-login`) |
| `KITE_PASSWORD` | login | `••••` | Zerodha password (automated `md-login`) |
| `KITE_TOKEN_BROKER_URL` | – | `https://calspread.online/api/kite/token` | Backend-only existing-session lookup; set with passcode |
| `KITE_TOKEN_BROKER_PASSCODE` | – | `••••` | Secret `x-token-passcode`; rotate and set with broker URL |
| `RISK_FREE_RATE` | – | `0.0691` | Legacy non-interactive fallback; staged UI/CLI ask the user to confirm the daily value |
| `KITE_STATIC_IP` | – | `203.0.113.7` | Source IP to bind Kite calls to (static-IP whitelist, Apr 2026) |
| `KITE_HTTP_PROXY` | – | `http://10.0.0.5:3128` | Proxy that egresses from the static IP (alternative to bind) |
| `INDICES` | – | `NIFTY,BANKNIFTY,FINNIFTY,SENSEX` | Index universe (default locked set) |
| `STOCK_UNIVERSE` | – | `all` | `all` or a comma allow-list |
| `CAPTURE_HZ` | – | `1` | Snapshot cadence (default 1) |
| `ZSTD_LEVEL` | – | `17` | EOD compression level |
| `MARKET_OPEN` / `MARKET_CLOSE` | – | `09:15` / `15:30` | Session window (IST) |
| `TIMEZONE` | – | `Asia/Kolkata` | Exchange timezone |
| `LOG_LEVEL` | – | `INFO` | Logging verbosity |
| `HTTP_HOST` | – | `127.0.0.1` | Loopback bind; use TLS + API authentication before exposing remotely |
| `HTTP_PORT` | ✅ | `9000` | Example seed for the backend HTTP/WS port — **no default**, env-only |
| `FRONTEND_URL` | ✅ | `http://localhost:<frontend-port>` | Frontend origin(s) for CORS (comma-separate for many) |

> **Ports are env-only.** `HTTP_PORT` has no code default; start the backend with
> `md-serve` (reads `HTTP_PORT`/`HTTP_HOST`). `FRONTEND_URL` sets the CORS allow-list and
> carries the frontend port, so no port is hardcoded anywhere. On the frontend side,
> `NEXT_PUBLIC_BACKEND_URL` is the single source for both HTTP and WebSocket URLs and must
> point at `http(s)://<host>:<HTTP_PORT>`.

> The `access_token` is **not** in `.env` — it is obtained by `md-login` and kept in
> session state ([[session-state]]), because it changes daily. The login *credentials*
> (`KITE_USER_ID` / `KITE_PASSWORD`) are seeded from `.env`
> so the automated login can run without a browser (algo_engine keeps these encrypted in
> Postgres; here they come from the environment).

## Automated login (`md-login`)

`app/kite/login.py` checks the shared token broker first, falls back to a headless Kite
login when the broker explicitly reports no active session, and writes today's state:

```
md-login                 # or:  python -m app.kite.login
md-login --date 2026-07-21 --rate 0.0691
```

Flow:
1. Backend calls the configured HTTPS broker with `x-token-passcode`. A valid access
   token is verified against Kite with this backend's API key and user id, then skips
   to step 4.
2. On an explicit unauthenticated broker response, `POST /api/login`
   `{user_id, password}` → `request_id`
3. The user enters the **TOTP** in the frontend or terminal; `POST /api/twofa`
   `{user_id, request_id, twofa_value}` verifies it
4. The user confirms the daily risk-free rate
5. For the fallback, `GET /connect/login?v=3&api_key=…` → `request_token`, then
   `POST api.kite.trade/session/token` with `checksum = SHA-256(api_key+request_token+api_secret)`
   → `access_token`, persisted to `_state/session-<date>.json`

All outbound Kite calls go through one client that can **bind `KITE_STATIC_IP`** or use
`KITE_HTTP_PROXY`, satisfying Kite's static-IP whitelist requirement (Apr 2026).

## `.gitignore` (must-haves)

```
.env
.env.local
.venv/
__pycache__/
node_modules/
MARKET_DATA/         # captured data — never committed
*.bin
*.bin.zst
```

## Settings object (backend)

`app/config.py` exposes a typed `Settings` (pydantic-settings) with the fields above,
plus derived live paths (`indices_dir`, `stocks_dir`, `indices_his_dir`,
`stocks_his_dir`, `instruments_dir`, `state_dir`) rooted at `MARKET_DATA_PATH`.
`ARCHIVE_DATA_PATH` is the separate destination for the same relative market-data
layout after verified zstd compression. Never log secrets.
