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
| `INDICES` | – | `NIFTY,BANKNIFTY,FINNIFTY,SENSEX` | Index universe (default locked set) |
| `STOCK_UNIVERSE` | – | `all` | `all` or a comma allow-list |
| `CAPTURE_HZ` | – | `1` | Snapshot cadence (default 1) |
| `ZSTD_LEVEL` | – | `17` | EOD compression level |
| `MARKET_OPEN` / `MARKET_CLOSE` | – | `09:15` / `15:30` | Session window (IST) |
| `TIMEZONE` | – | `Asia/Kolkata` | Exchange timezone |
| `LOG_LEVEL` | – | `INFO` | Logging verbosity |
| `HTTP_PORT` | – | `8000` | FastAPI port |

> `access_token` and the daily **bond yield** are **not** in `.env` — they are entered
> at login and kept in session state ([[session-state]]), because they change daily.

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
