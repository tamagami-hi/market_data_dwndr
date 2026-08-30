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

> **Implementation status:** The combined `session-<date>.json` and monolithic cancel/flush
> behavior describe current code. Credential revisions, separated durable/runtime state, and
> `AUTH_BLOCKED` describe the locked distributed target and are not implemented yet.

The Calspread-held `access_token` (and the **risk-free rate** stamped into headers) must
survive a mid-day restart, so the current implementation holds them in a small persisted
**session-state** file. Calspread is the sole Kite login/session authority; this downloader
retrieves and validates the token that already exists. Repeated retrieval normally returns the
same token until an administrator relogs in through Calspread. The risk-free rate comes from
the most recent permitted session or its configured source.

## Location & shape

`MARKET_DATA/_state/session-<YYYY-MM-DD>.json`:
```jsonc
{
  "trading_date": "2026-07-21",
  "access_token": "…",          // day's Kite token
  "access_token_at": 1753070400000, // legacy name: downloader observation time, not issuance
  "risk_free_rate": 0.0691,      // risk-free rate fetched daily from calspread (decimal)
  "risk_free_rate_as_of": "2026-07-21",
  "started_at": 1753070400000
}
```

## Rules

- Written atomically after token validation; read on every (re)start. It is a restart cache,
  not the session authority.
- The risk-free rate is fetched once per trading day from the calspread broker
  (env `RISK_FREE_RATE` is the fallback) and stamped as of the trading date.
- The **risk-free rate** from here is stamped into every file header for the day
  ([[bin-structure-spec]]) so each `.bin` is self-contained.
- On restart, if a session file exists for today with a valid token, **reuse it** — do
  not re-prompt. Capture appends to today's files (headers are only written when a file
  is empty, so no duplicate header).
- A feed stall, disconnect, or process restart reuses the same token. Feed recovery never
  invalidates session state and never asks Calspread for a “fresh” token.
- If Kite explicitly rejects the token, the current monolith cancels capture and flushes its
  writers. In the distributed target, all uplinks on the confirmed rejected revision stop while
  writer/tickbus/control remain live; control preserves the durable record, revokes the runtime
  projection, and marks `AUTH_BLOCKED`. Calspread will keep returning the same rejected token
  until an administrator relogs there, so repeated lookup is not recovery. Capture resumes only
  after a different token is retrieved and validates.
- Never commit `_state/` (it lives under the gitignored `MARKET_DATA/`).

## Why not just `.env`

The access token's lifetime is controlled externally by Kite/Calspread and must not be inferred
from the downloader's trading date or retrieval time. The risk-free rate is daily. Keeping both
out of normal configuration avoids stale secrets and keeps the effective rate next to the data
it stamps.
`RISK_FREE_RATE` remains only an optional fallback when the rate broker is unavailable.

## Target state split

The distributed target separates credential lifetime from daily capture metadata:

```text
/var/lib/md-control/broker-credential.json
                                # private durable volume: token, revision, fingerprint,
                                # status, retrieved_at, validated_at
_state/session-<date>.json      # trading-date and risk-free-rate metadata
/run/mdsession/current.json     # minimal read-only projection for WS/REST uplinks
```

`retrieved_at` and `validated_at` are downloader observations, not Kite issuance or rotation
times. A new trading date may reuse the same token. See [[broker-session-authentication]].
