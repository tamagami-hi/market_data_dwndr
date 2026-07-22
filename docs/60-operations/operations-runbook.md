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

## Time & timezone

- The exchange operates in **IST (Asia/Kolkata)**.
- Timestamps are **stored as Unix epoch milliseconds (UTC)** in every frame; the UI
  converts to IST for display. File dates (`<YYYY-MM-DD>.bin`) use the **IST trading
  date**.

## Market calendar & hours

- **Configured downloader window:** 09:00–15:30 IST. Capture stops and writers flush
  exactly at the 15:30 boundary.
- **Auth reset:** Kite invalidates tokens daily ~06:00 IST.
- **Holidays:** trading holidays vary; source a holiday list (Kite/NSE calendar) or a
  maintained config so the scheduler skips non-trading days. On holidays the service
  idles (no files created).
- The scheduler captures only during its configured window; it rolls files and
  compresses at/after close.

## Automated morning cycle

1. From 06:30 until 08:30 the backend remains idle and does not call the shared-token
   service.
2. From 08:30 until 09:00 it polls the configured `calspread.online` HTTPS endpoint at
   the env-seeded interval. A returned token is validated with Kite before persistence.
3. At 09:00 capture starts automatically if today's token and an allowed 10-year
   government bond yield are available. The latest yield is reused the following
   Monday–Friday market day; weekends do not count, and on the third market day it must
   be updated before capture can start.
4. If the shared token remains unavailable, use the terminal `md-login` TOTP flow as
   an explicit operational fallback. The frontend only reports initialization state.
5. For a late market-day start, the backend validates the newest saved token first. If
   it fails, broker polling resumes during market hours until a valid token is found.

## During the session

- Watch the **Capture Monitor** ([[frontend]]): per-underlying WS health, frames
  written, file sizes, 1 Hz heartbeat, disk usage.
- Reconnects are automatic (backoff + circuit breaker, [[live-data-pipeline]]).
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
  `access_token` + bond yield and **appends** to today's already-open files (header is
  written only when a file is empty). No re-prompt, no duplicate headers.

## Shutdown

- Graceful stop flushes and closes all writers before exit; if the day is over it runs
  the compression sweep first.

## Related failure handling

See [[failure-modes]] for disk-full, mid-day auth expiry, stall/reconnect, and
truncated-file recovery.
