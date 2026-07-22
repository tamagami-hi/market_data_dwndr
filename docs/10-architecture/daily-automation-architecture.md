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
      ├── CaptureController ◀── capture-ready token + yield
      │
      └── stop/flush ── EOD compressor ── verified HDD archive
```

The broker client is the existing bounded `httpx` implementation of the curl-equivalent
GET request. No shell subprocess receives the passcode. Manual login remains isolated in
`LoginCoordinator` and is never invoked automatically.

One process owns automation. CaptureController locking makes UI and scheduled actions
idempotent. EOD work runs outside the event loop and remains safe to repeat after a
restart.
