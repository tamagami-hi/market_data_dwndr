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
| `KITE_USER_ID` | login | `AB1234` | Zerodha login id (automated `md-login`) |
| `KITE_PASSWORD` | login | `••••` | Zerodha password (automated `md-login`) |
| `KITE_TOTP_SECRET` | – | `JBSWY3DP…` | Base32 TOTP secret; if unset, TOTP is prompted in the terminal |
| `RISK_FREE_RATE` | – | `0.0691` | 10-yr bond yield (decimal); prompted by `md-login` if unset |
| `KITE_STATIC_IP` | – | `203.0.113.7` | Source IP to bind Kite calls to (static-IP whitelist, Apr 2026) |
| `KITE_HTTP_PROXY` | – | `http://10.0.0.5:3128` | Proxy that egresses from the static IP (alternative to bind) |
| `INDICES` | – | `NIFTY,BANKNIFTY,FINNIFTY,SENSEX` | Index universe (default locked set) |
| `STOCK_UNIVERSE` | – | `all` | `all` or a comma allow-list |
| `CAPTURE_HZ` | – | `1` | Snapshot cadence (default 1) |
| `ZSTD_LEVEL` | – | `17` | EOD compression level |
| `MARKET_OPEN` / `MARKET_CLOSE` | – | `09:15` / `15:30` | Session window (IST) |
| `TIMEZONE` | – | `Asia/Kolkata` | Exchange timezone |
| `LOG_LEVEL` | – | `INFO` | Logging verbosity |
| `HTTP_PORT` | – | `8000` | FastAPI port |

> The `access_token` is **not** in `.env` — it is obtained by `md-login` and kept in
> session state ([[session-state]]), because it changes daily. The login *credentials*
> (`KITE_USER_ID` / `KITE_PASSWORD` / optional `KITE_TOTP_SECRET`) are seeded from `.env`
> so the automated login can run without a browser (algo_engine keeps these encrypted in
> Postgres; here they come from the environment).

## Automated login (`md-login`)

`app/kite/login.py` performs a headless Kite login and writes today's session state:

```
md-login                 # or:  python -m app.kite.login
md-login --date 2026-07-21 --rate 0.0691
```

Flow (ported from algo_engine's OAuth, automated end-to-end):
1. `POST /api/login` `{user_id, password}` → `request_id`
2. `POST /api/twofa` `{user_id, request_id, twofa_value}` — the **TOTP** is generated
   from `KITE_TOTP_SECRET` if set, otherwise **entered in the terminal**
3. `GET /connect/login?v=3&api_key=…` → follow redirects → `request_token`
4. `POST api.kite.trade/session/token` with `checksum = SHA-256(api_key+request_token+api_secret)`
   → `access_token`, persisted to `_state/session-<date>.json`

All outbound Kite calls go through one client that can **bind `KITE_STATIC_IP`** or use
`KITE_HTTP_PROXY`, satisfying Kite's static-IP whitelist requirement (Apr 2026).

## `.gitignore` (must-haves)

```
.env
.venv/
__pycache__/
node_modules/
MARKET_DATA/         # captured data — never committed
*.bin
*.bin.zst
```

## Settings object (backend)

`app/config.py` exposes a typed `Settings` (pydantic-settings) with the fields above,
plus derived paths (`indices_dir`, `stocks_dir`, `indices_his_dir`, `stocks_his_dir`,
`instruments_dir`, `state_dir`) rooted at `MARKET_DATA_PATH`. Never log secrets.
