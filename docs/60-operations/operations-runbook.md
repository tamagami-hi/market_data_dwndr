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

- **NSE/BSE regular session:** 09:15–15:30 IST (pre-open 09:00–09:15).
- **Auth reset:** Kite invalidates tokens daily ~06:00 IST.
- **Holidays:** trading holidays vary; source a holiday list (Kite/NSE calendar) or a
  maintained config so the scheduler skips non-trading days. On holidays the service
  idles (no files created).
- The scheduler only captures during the regular session; it rolls files and compresses
  at/after close.

## Morning start (~06:30 IST)

1. Start the backend; open the **login link** it prints/serves.
2. Authorize on Kite → the app exchanges `request_token` → `access_token` (using the
   secret from [[config-and-env]]).
3. **Enter the day's 10-yr bond yield** on the same screen.
4. Both are persisted to session state ([[session-state]]) and the bond yield is
   stamped into every file header written today.
5. At 09:15 the capture engine subscribes and begins writing 1 Hz frames.

## During the session

- Watch the **Capture Monitor** ([[frontend]]): per-underlying WS health, frames
  written, file sizes, 1 Hz heartbeat, disk usage.
- Reconnects are automatic (backoff + circuit breaker, [[live-data-pipeline]]).

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
