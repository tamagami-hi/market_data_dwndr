---
title: Operations Runbook
area: operations
type: spec
status: locked
tags: [area/operations, type/spec, status/locked]
up: "[[Operations-MOC]]"
related: ["[[config-and-env]]", "[[session-state]]", "[[failure-modes]]", "[[live-data-pipeline]]", "[[build-guide]]"]
---

# Operations Runbook

The daily lifecycle of the capture service.

> **Implementation status:** Market capture/EOD instructions describe the current service.
> The Calspread-only `WAITING_FOR_ADMIN`, credential-revision, and `AUTH_BLOCKED` behavior below
> is the locked target design and is not implemented yet. Current direct-login/invalidation
> behavior is legacy drift; see [[broker-session-authentication]] and [[Build-Status]].

## Time & timezone

- The exchange operates in **IST (Asia/Kolkata)**.
- Timestamps are **stored as Unix epoch milliseconds (UTC)** in every frame; the UI
  converts to IST for display. File dates (`<YYYY-MM-DD>.bin`) use the **IST trading
  date**.

## Market calendar & hours

- **Configured downloader window:** 09:00–15:30 IST. Capture stops and writers flush
  exactly at the 15:30 boundary.
- **Session authority:** Calspread owns Kite login and the active access token. Do not infer
  token rotation from the clock or trading-date rollover.
- **Holidays:** trading holidays vary; source a holiday list (Kite/NSE calendar) or a
  maintained config so the scheduler skips non-trading days. On holidays the service
  idles (no files created).
- The scheduler captures only during its configured window; it rolls files and
  compresses at/after close.

## Calspread session synchronization

1. From 06:30 until 08:30 the backend remains idle and does not call the Calspread session
   endpoint.
2. From 08:30 until 09:00 it polls the configured `calspread.online` HTTPS endpoint at
   the env-seeded interval. This retrieves Calspread's existing token; it does not log in or
   create a new one. A returned token is validated with Kite before persistence.
3. At 09:00 capture starts automatically once today's token and risk-free rate are
   available. The risk-free rate is fetched daily from the calspread broker (env
   `RISK_FREE_RATE` is the fallback).
4. If Calspread has no active session, the frontend/status reports
   `WAITING_FOR_ADMIN`. An administrator must log in to Kite through Calspread; there is no
   downloader-owned password/TOTP fallback in the target architecture.
5. For a late market-day start, the backend validates the newest saved token first. If
   Kite rejects it, bounded synchronization only watches for a different token created by an
   administrator relogin in Calspread; the unchanged token remains `AUTH_BLOCKED`.

## During the session

- Watch the **Capture Monitor** ([[frontend]]): per-underlying WS health, frames
  written, file sizes, 1 Hz heartbeat, disk usage.
- Reconnects are automatic (backoff + circuit breaker, [[live-data-pipeline]]).
- Feed reconnects reuse the same API key/access-token pair and do not call Calspread.
- API ingestion and BIN persistence are the protected hot path. Frontend WS updates
  are best-effort and may be delayed or coalesced for slow clients; they never apply
  backpressure to market-data fetching or disk writes.

## End of day (after 15:30 IST)

1. Capture engine stops subscribing; writers flush and close today's files.
2. **EOD compression sweep:** each `<date>.bin` under `MARKET_DATA_PATH` is written to
   the same relative path plus `.zst` under `ARCHIVE_DATA_PATH` (zstd L17). The raw is
   removed only after destination-side verification ([[bin-format]]).
3. Instrument-master archive for the day is retained under `_instruments/`.
4. Session state for the day is finalized.

## Restart mid-day

- On restart, the service loads session state ([[session-state]]): reuses the day's
  `access_token` + risk-free rate and **appends** to today's already-open files (header is
  written only when a file is empty). No re-prompt, no duplicate headers.

## Authentication blocked

Use this procedure only for an explicit Kite authentication rejection, not for a stale or
disconnected feed:

1. Confirm status reports `AUTH_BLOCKED` and note the redacted credential revision.
2. Do not loop-restart the backend or request a “refresh”; Calspread will return the same token.
3. Have an administrator log in to Kite again through `calspread.online`.
4. `md-control` retrieves the changed token, validates it, atomically advances the credential
   revision, and resumes the uplinks.
5. Confirm the new revision is ready and market-data timestamps advance. Never paste or log the
   access token.

See [[broker-session-authentication]].

## Shutdown

- Graceful stop flushes and closes all writers before exit; if the day is over it runs
  the compression sweep first.

## Related failure handling

See [[failure-modes]] for disk-full, explicit Kite authentication rejection, stall/reconnect, and
truncated-file recovery.
