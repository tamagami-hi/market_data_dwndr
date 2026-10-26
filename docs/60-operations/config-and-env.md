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

> **Implementation status:** The variable table distinguishes current legacy requirements from
> target settings. `KITE_EXPECTED_USER_ID`, credential revisions, and `AUTH_BLOCKED` are target
> design and are not implemented yet; current direct-login settings remain transitional.

## `.env` variables

| Var | Required | Example | Purpose |
|---|---|---|---|
| `KITE_API_KEY` | ✅ | `abcd1234` | Kite Connect app key |
| `KITE_EXPECTED_USER_ID` | target | `AB1234` | Non-secret account binding used by `md-control` to reject a token for the wrong broker account |
| `KITE_API_SECRET` | legacy code only | `wxyz5678` | Current direct-login implementation still requires it; target downloader removes it because token exchange belongs to Calspread |
| `MARKET_DATA_PATH` | ✅ | `/data/MARKET_DATA` | Output root ([[storage-layout]]) |
| `ARCHIVE_DATA_PATH` | ✅ | `/data/z_market_data` | Separate root for verified `.bin.zst` archives |
| `KITE_USER_ID` | legacy code only | `AB1234` | Current login-oriented setting; target replaces its validation role with non-secret `KITE_EXPECTED_USER_ID` |
| `KITE_PASSWORD` | legacy code only | `••••` | Must move out of this downloader; Calspread owns broker login |
| `KITE_TOKEN_BROKER_URL` | – | `https://<subdomain>.beonedge.in/api/v1/token` | The broker login service's token API, which hands out the Kite token it logged in for `KITE_USER_ID`; set with the three below |
| `KITE_TOKEN_BROKER_CF_CLIENT_ID` | with the URL | `••••.access` | Cloudflare Access service token Client ID the token API's Access policy allows |
| `KITE_TOKEN_BROKER_CF_CLIENT_SECRET` | with the URL | `••••` | That service token's Client Secret |
| `KITE_TOKEN_BROKER_TOTP_SEED` | with the URL | `••••` | The account's token API seed (base32), shown once by the broker login dashboard; each request sends its current code. Not the Zerodha login seed |
| `KITE_TOKEN_BROKER_PASSCODE` | with `KITE_RATE_BROKER_URL` | `••••` | `x-token-passcode` for the Calspread risk-free-rate endpoint; it is not a Kite credential |
| `RELEASE_MAINTENANCE_TOKEN` | release | `••••` | At least 32 characters; authenticates the internal capture drain API |
| `RELEASE_MAINTENANCE_TTL_SECONDS` | – | `900` | Persistent drain lease lifetime; release tooling requires 600–900 seconds |
| `RISK_FREE_RATE` | – | `0.0691` | Fallback risk-free rate (decimal) used only when `KITE_RATE_BROKER_URL` is unavailable |
| `INDICES` | – | `NIFTY,BANKNIFTY,FINNIFTY,SENSEX` | Index universe (default locked set) |
| `MARKET_HOLIDAYS` | – | `2026-08-15,2026-10-02` | Comma-separated exchange closure dates (`YYYY-MM-DD`); no broker polling, capture, or EOD on these dates |
| `STOCK_UNIVERSE` | – | `all` | `all` or a comma allow-list |
| `CAPTURE_HZ` | – | `1` | Snapshot cadence (default 1) |
| `ZSTD_LEVEL` | – | `17` | EOD compression level |
| `AUTH_POLL_START` / `AUTH_POLL_END` | – | `08:30` / `09:00` | Legacy names for the trading-day Calspread session-sync window (IST) |
| `AUTH_POLL_INTERVAL_SECONDS` | – | `60` | Delay between existing-session lookup attempts inside the sync window |
| `MARKET_OPEN` / `MARKET_CLOSE` | – | `09:00` / `15:30` | Automated capture window (IST; close is exclusive) |
| `TIMEZONE` | – | `Asia/Kolkata` | Exchange timezone |
| `LOG_LEVEL` | – | `INFO` | Logging verbosity |
| `HTTP_HOST` | – | `127.0.0.1` | Loopback bind; use TLS + API authentication before exposing remotely |
| `HTTP_PORT` | ✅ | `9000` | Example seed for the backend HTTP/WS port — **no default**, env-only |
| `FRONTEND_URL` | ✅ | `http://localhost:<frontend-port>` | Frontend origin(s) for CORS and WebSocket Origin checks (comma-separate for many) |

> **Ports are env-only.** `HTTP_PORT` has no code default; start the backend with
> `md-serve` (reads `HTTP_PORT`/`HTTP_HOST`). `FRONTEND_URL` sets the CORS allow-list and
> carries the frontend port, so no port is hardcoded anywhere. On the frontend side,
> `NEXT_PUBLIC_BACKEND_URL` is the single source for both HTTP and WebSocket URLs and must
> point at `http(s)://<host>:<HTTP_PORT>`.

> The `access_token` is **not** in `.env`. Calspread is the sole Kite login/session authority,
> and this downloader only retrieves and caches Calspread's existing token. Retrieval does not
> imply rotation; only administrator relogin through Calspread changes the token. The target
> downloader stores no Kite API secret, password, TOTP, or request-token exchange capability.
> Current settings and login commands that require them are legacy implementation drift.

## Private VPS access

There is no separate browser operator-authentication layer. HTTP APIs are reachable to
clients that can reach the backend, and WebSocket handshakes enforce the browser origin
allow-list from `FRONTEND_URL`. CORS and Origin checks are not user authentication, so
bind the backend and frontend only to loopback, Tailscale, or another trusted private
network. The release-maintenance API keeps its dedicated
`X-Release-Maintenance-Token` credential.

## Daily Calspread session synchronization

On trading days the backend does not contact the Calspread session endpoint before 08:30. From
08:30 (inclusive) until 09:00 (exclusive), it periodically calls the configured HTTPS
endpoint, validates any returned token directly with Kite, and persists it. Capture
starts at 09:00 only when the token and the risk-free rate are
present. The target fallback is administrator relogin through Calspread, not a local TOTP flow.

This polling retrieves an already-established session. Repeated calls normally return the same
token, including across backend restarts. A feed stall never invokes this flow. If Kite
explicitly rejects the current token, the system enters `AUTH_BLOCKED` and an administrator
must relogin through Calspread; retrieving the unchanged token cannot recover capture.

On configured market holidays and weekends, the backend does not poll the broker,
start capture, or run EOD work. Keep `MARKET_HOLIDAYS` synchronized with the official
exchange calendar before each calendar year.

If the backend starts late on a market day, it first validates the most recent persisted access
token. A working cache remains usable when Calspread is temporarily unavailable. If Kite rejects
the cached token, session synchronization continues only to observe an administrator-created
change; the same token remains blocked. Capture starts when a different token is available and
validates.

The risk-free rate is fetched once per trading day from `KITE_RATE_BROKER_URL` (it
returns a percent, which the backend converts to a decimal, reusing the
`x-token-passcode`). `RISK_FREE_RATE` is used only as a fallback when that broker is
unavailable.

## Release-maintenance lease

Before replacing the backend container, the release manager acquires
`POST /api/capture/maintenance` with `X-Release-Maintenance-Token`. The backend first
atomically persists a bounded lease under `_state/release-maintenance.json`, then stops
capture and waits for its writer task to flush before returning the opaque `lease_id`.
While the lease is valid, manual and scheduled capture starts are rejected. After the
deployment, the manager releases it with
`DELETE /api/capture/maintenance/<lease_id>`. A lease survives container restart but
expires automatically, so an interrupted deployment cannot block capture indefinitely.

Generate the shared secret with `openssl rand -hex 32`. Keep it in ignored environment
files and pass it to the request without placing its value in logs or process arguments.

## Legacy local-login path

The current repository still contains `md-login`, Kite password/TOTP handling, and
`request_token` → `access_token` exchange code. It conflicts with the target trust boundary
and must not be treated as the operational fallback or copied into new services.

In the target architecture, an unauthenticated or rejected Calspread session produces
`WAITING_FOR_ADMIN` or `AUTH_BLOCKED`. The operator logs in through `calspread.online`; this
downloader then retrieves and validates the resulting token. Removal of the legacy path is a
separate code migration and is not implemented by this documentation change.

Market-data Kite calls use `KITE_API_KEY + access_token`. This project only downloads
market data (quotes, instruments, WebSocket ticker), so it does **not** require a
static egress IP — Kite's static-IP whitelist (Apr 2026) applies only to order-placement
endpoints, which live in the separate `algo_engine` service.

See [[broker-session-authentication]] for the canonical authority, state-machine, and secret
distribution design.

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
