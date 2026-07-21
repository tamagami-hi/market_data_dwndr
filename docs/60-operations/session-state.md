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

The two daily values that are **not** in `.env` — the Kite `access_token` and the
**10-yr bond yield** — are entered at login and must survive a mid-day restart. They
are held in a small persisted **session-state** file.

## Location & shape

`MARKET_DATA/_state/session-<YYYY-MM-DD>.json`:
```jsonc
{
  "trading_date": "2026-07-21",
  "access_token": "…",          // day's Kite token
  "access_token_at": 1753070400000,
  "risk_free_rate": 0.0691,      // 10-yr bond yield entered at login (decimal)
  "started_at": 1753070400000
}
```

## Rules

- Written once at morning login; read on every (re)start.
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

`access_token` and bond yield change **every day** and are entered interactively;
keeping them out of `.env` avoids stale secrets in config and keeps the daily value
next to the data it stamps.
