# Daily automation architecture

`DailyAutomationService` is a single FastAPI-lifespan background task. A pure decision
function maps the current IST time plus persisted state to one action; side effects stay
in injected broker, session, capture, and EOD adapters.

```text
TradingCalendar
      │
      ▼
DailyAutomationService ── Calspread session lookup ── validated existing session
      │                         │
      ├── CaptureController ◀── capture-ready token + risk-free rate
      │
      └── stop/flush ── EOD compressor ── verified HDD archive
```

`TradingCalendar` applies timezone, open/close bounds, weekends, and the env-driven
`MARKET_HOLIDAYS` set before any broker, capture, or EOD action is emitted.

Calspread is the sole Kite login/session authority. The bounded `httpx` client only retrieves
the access token already held by Calspread; it does not log in, exchange a request token, or
rotate credentials. Repeating the GET normally returns the same token until an administrator
relogs in through Calspread. No shell subprocess receives the Calspread passcode.

One process owns automation. `CaptureController` locking serializes scheduler and release-
maintenance lifecycle changes; the browser has no manual Start/Stop control. A feed stall or
disconnect restarts transport with the same cached token and never contacts Calspread. A typed
Kite authentication failure currently cancels the monolithic engine and waits for writer
cleanup. In the distributed target, `md-control` independently confirms the rejection, stops
all uplinks on that credential revision, keeps the writer/tickbus/control live, and enters
`AUTH_BLOCKED`. Re-reading the same Calspread token cannot recover it; capture resumes only
after an administrator relogs in through Calspread and the resulting different token is
validated and persisted. Non-auth
capture failures remain sticky so disk/writer faults cannot be mistaken for token
expiry. EOD work runs outside the event loop and remains safe to repeat after a restart.

The target architecture contains no downloader-owned Kite password/TOTP or API-secret login
fallback. Current direct-login paths are legacy implementation drift. See
[[broker-session-authentication]].
