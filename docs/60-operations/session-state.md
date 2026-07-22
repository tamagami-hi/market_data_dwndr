---
title: Session State & Resume
area: operations
type: spec
status: locked
tags: [area/operations, type/spec, status/locked]
up: "[[Operations-MOC]]"
related: ["[[operations-runbook]]", "[[config-and-env]]", "[[failure-modes]]", "[[bin-format]]"]
---

# Session State & Resume

The daily `access_token` (and the **10-yr bond yield** stamped into headers) must
survive a mid-day restart, so they are held in a small persisted **session-state**
file. The morning automation normally writes it after validating the shared broker
token; the manual `md-login`/TOTP flow remains a fallback. The bond yield comes from
the most recent permitted session or an explicit frontend/terminal update.

## Location & shape

`MARKET_DATA/_state/session-<YYYY-MM-DD>.json`:
```jsonc
{
  "trading_date": "2026-07-21",
  "access_token": "…",          // day's Kite token
  "access_token_at": 1753070400000,
  "risk_free_rate": 0.0691,      // 10-yr bond yield entered at login (decimal)
  "risk_free_rate_as_of": "2026-07-21",
  "rate_update_required": false,
  "started_at": 1753070400000
}
```

## Rules

- Written atomically after daily token validation; read on every (re)start.
- The latest yield may be reused the next Monday–Friday market day. Weekends do not
  count; on the third market day it is stale and capture stays blocked until updated.
- The **bond yield** from here is stamped into every file header for the day
  ([[bin-structure-spec]]) so each `.bin` is self-contained.
- On restart, if a session file exists for today with a valid token, **reuse it** — do
  not re-prompt. Capture appends to today's files (headers are only written when a file
  is empty, so no duplicate header).
- If the token is missing/expired mid-day, the service pauses capture and surfaces a
  re-login prompt ([[failure-modes]]); once re-authorized, session state is updated and
  capture resumes on the same files.
- Never commit `_state/` (it lives under the gitignored `MARKET_DATA/`).

## Why not just `.env`

The `access_token` changes daily and the bond yield has explicit freshness state.
Keeping them out of the normal configuration path avoids stale secrets and keeps the
effective yield next to the data it stamps. `RISK_FREE_RATE` remains only an optional
legacy bootstrap fallback.
