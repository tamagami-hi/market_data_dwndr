# Daily automation system design

## State windows (IST)

| Window | Automatic action |
|---|---|
| Before 08:30 | Idle; zero broker traffic |
| 08:30–09:00 | Poll and validate the shared token at the configured cadence |
| 09:00–15:30 | Start/resume capture when the daily session is capture-ready |
| At/after 15:30 | Stop and await writers, then run idempotent EOD compression |

Weekends and configured holidays do not authenticate or capture. A startup after close
may run EOD to repair raw files left by an interrupted prior process.

## Session readiness

Daily session state records `risk_free_rate_as_of` alongside the token. The risk-free
rate is fetched once per trading day from the calspread broker (env `RISK_FREE_RATE` is
the fallback), so there is no freshness/expiry rule or operator update step.

## Failure policy

Broker network/unauthenticated/invalid-token results are redacted and retried only
inside the auth window. Failure never triggers password/TOTP automation. Capture and EOD
are serialized; an EOD failure retains the raw file.
