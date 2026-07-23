# Daily automation architecture

`DailyAutomationService` is a single FastAPI-lifespan background task. A pure decision
function maps the current IST time plus persisted state to one action; side effects stay
in injected broker, session, capture, and EOD adapters.

```text
TradingCalendar
      │
      ▼
DailyAutomationService ── broker client ── validated daily session
      │                         │
      ├── CaptureController ◀── capture-ready token + risk-free rate
      │
      └── stop/flush ── EOD compressor ── verified HDD archive
```

`TradingCalendar` applies timezone, open/close bounds, weekends, and the env-driven
`MARKET_HOLIDAYS` set before any broker, capture, or EOD action is emitted.

The broker client is the existing bounded `httpx` implementation of the curl-equivalent
GET request. No shell subprocess receives the passcode. Manual login remains isolated in
`LoginCoordinator` and is never invoked automatically.

One process owns automation. `CaptureController` locking serializes scheduler and release-
maintenance lifecycle changes; the browser has no manual Start/Stop control. A typed
Kite authentication failure cancels the engine, waits for writer cleanup, and
invalidates only the persisted token used by that capture. The next scheduler tick
therefore returns to the existing broker-fetch action; once the replacement token is
validated and persisted, capture starts again without a backend restart. Non-auth
capture failures remain sticky so disk/writer faults cannot be mistaken for token
expiry. EOD work runs outside the event loop and remains safe to repeat after a restart.
