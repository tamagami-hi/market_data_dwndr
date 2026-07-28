# TickVault Frontend Redesign System Design

## Monitor data flow

```text
/ws/capture-status -> guard -> live rows/global/compression -> live sections
/ws/session        -> guard -> bounded log buffer          -> log strip/dialog
/api/stats         -> guard -> history/retained snapshot   -> historical sections
/api/capture/history -> guard -> storage history            -> storage section
```

The first poll starts immediately. Each later poll is scheduled only after the previous request settles, so requests never overlap. Each request receives an `AbortSignal`; unmount or cadence changes abort in-flight work.

Active capture and auth-window polling use the existing fast cadence. Idle polling uses the existing one-minute cadence. Noncritical REST work pauses while `document.hidden` is true and refreshes immediately when the document becomes visible.

## Ownership and fallback

- While capture is live, WebSocket capture telemetry is authoritative.
- While capture is idle, a valid persisted monitor snapshot replaces the display.
- A successful refresh records its completion time.
- A failed refresh retains the last valid data, marks it stale, and displays the error.
- A later success clears the error and records recovery without resetting valid live data.

## Freshness states

`loading -> live | idle | retained | past-session`

Any valid state can also be `stale`, `degraded`, or `exhausted`. REST failures add an error condition while retaining valid data. Malformed payloads are rejected at the adapter and surface a degraded-data message instead of crashing.

## Immutable live updates

Option deltas copy the selected symbol, calls or puts block, changed columns, and changed array positions only. Stock boards are replaced as immutable snapshots and projected with pure helpers. Stable row props and memo comparators isolate 1 Hz updates.

## Failure behavior

- Disconnected WebSocket topics expose disconnected or stale transport state.
- REST failures show the failed operation and last successful refresh.
- Missing or older optional fields receive documented defaults.
- Required malformed identity fields reject the payload and preserve the last valid snapshot.
