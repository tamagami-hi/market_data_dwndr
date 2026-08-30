# Daily automation system design

## State windows (IST)

| Window | Automatic action |
|---|---|
| Before 08:30 | Idle; zero broker traffic |
| 08:30–09:00 | Retrieve and validate Calspread's existing token at the configured cadence |
| 09:00–15:30 | Start/resume capture when the daily session is capture-ready |
| At/after 15:30 | Stop and await writers, then run idempotent EOD compression |

Weekends and configured holidays do not authenticate or capture. A startup after close
may run EOD to repair raw files left by an interrupted prior process.

## Session readiness

Calspread is the sole Kite login/session authority. Retrieval is idempotent and normally
returns the same token until an administrator relogs in through Calspread. Daily session state
records `risk_free_rate_as_of` alongside the locally cached token. The risk-free
rate is fetched once per trading day from the calspread broker (env `RISK_FREE_RATE` is
the fallback), so there is no freshness/expiry rule or operator update step.

## Failure policy

Calspread network and malformed responses are redacted. If a previously validated cached token
still works, a Calspread outage degrades the session source but does not stop capture. If no
usable token exists, the system waits for Calspread rather than starting a downloader-owned
password/TOTP flow.

An explicit Kite rejection enters `AUTH_BLOCKED`: the same token remains blocked regardless of
how often it is retrieved. Operator status instructs an administrator to relogin through
Calspread; a different token must validate before capture resumes. Feed failures never change
credential state. Capture and EOD are serialized; an EOD failure retains the raw file. See
[[broker-session-authentication]].
