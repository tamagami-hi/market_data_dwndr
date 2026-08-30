---
title: Broker Session Authentication
area: operations
type: spec
status: locked
tags: [area/operations, type/spec, status/locked, auth, calspread, sessions]
up: "[[Operations-MOC]]"
related: ["[[session-state]]", "[[config-and-env]]", "[[operations-runbook]]", "[[failure-modes]]", "[[distributed-capture-architecture-plan]]"]
---

# Broker Session Authentication

This is the canonical authentication model for the target architecture. It corrects older
documentation that described Calspread retrieval as access-token acquisition or refresh.
The current code still contains legacy direct-login, TOTP, invalidation, and refresh-named
paths; removing them is a future implementation task. This document changes the design, not
the running code.

## Authority and trust boundary

**Calspread is the sole Kite login and session authority.** An administrator logs in to Kite
through `calspread.online`. Calspread uses the Kite API key and API secret to complete the
broker login and retains the access token returned by Kite.

`market_data_dwndr` does not log in to Kite and cannot create, exchange, refresh, or rotate an
access token. It retrieves the token currently held by Calspread, validates it with Kite, caches
it for restart continuity, and uses `api_key + access_token` for REST and WebSocket market-data
connections.

Retrieval is read-only and idempotent. Repeated calls to Calspread return the same token until
an administrator completes another broker login through Calspread. A backend restart, uplink
restart, WebSocket reconnect, REST fallback, date rollover, or repeated Calspread lookup does
not itself change the token.

Kite remains authoritative for whether an API-key/token pair is valid. Calspread is
authoritative for the token value this downloader is allowed to retrieve. `md-control` owns
local policy, validation, persistence, and credential revision; it is not a second login
authority.

## Credential ownership

| Credential or value | Owner | Consumers |
|---|---|---|
| Kite API secret | Calspread only | Calspread login flow |
| Broker password and TOTP | Calspread/admin only | Calspread login flow |
| Calspread endpoint passcode | `md-control` only | Calspread session lookup |
| Kite API key | Calspread, `md-control`, and market-data uplinks | Token issuance binding, validation, REST, and WS |
| Expected Kite account id (`KITE_EXPECTED_USER_ID`) | `md-control` configuration | Non-secret account-binding validation; distinct from legacy login user id |
| Kite access token | Calspread; cached by `md-control` | `md-control` validation; WS and REST uplinks |
| Risk-free rate | Calspread rate endpoint or configured fallback | control, writer/session metadata |

The token and passcode must never appear in logs, metrics, ZMQ health messages, session headers,
browser APIs, or application control-plane/browser-visible URLs. Kite's SDK-required WebSocket
handshake transport must be redacted from logs. Writer, tickbus, stream, and frontend services
do not need credential mounts. `md-control` projects the active credential through a narrow
runtime file or equivalent secret channel mounted read-only into WS/REST uplinks.

The API key used by `md-control` and every uplink must be the same Kite application key under
which Calspread obtained the access token. A token that validates for a different application
or broker account is not an adoptable credential revision.

## State machine

```text
NO_TOKEN
  ├─ source unavailable/malformed ────────────────▶ NO_TOKEN, source degraded
  ├─ Calspread has no active session ─────────────▶ WAITING_FOR_ADMIN
  ├─ candidate fails validation ──────────────────▶ NO_TOKEN, candidate rejected
  └─ candidate validates with Kite ───────────────▶ READY(revision N)

WAITING_FOR_ADMIN
  ├─ Calspread still has no session ──────────────▶ WAITING_FOR_ADMIN
  ├─ source unavailable/malformed ────────────────▶ WAITING_FOR_ADMIN, source degraded
  ├─ candidate fails validation ──────────────────▶ WAITING_FOR_ADMIN, candidate rejected
  └─ candidate validates with Kite ───────────────▶ READY(revision N)

READY(revision N)
  ├─ process/socket restart ──────────────────────▶ READY(revision N)
  ├─ WS stall or REST transport error ────────────▶ READY(revision N)
  ├─ Calspread returns the same token ────────────▶ READY(revision N)
  ├─ Calspread unavailable/unauthenticated ───────▶ READY(revision N), source degraded
  ├─ different candidate fails validation ────────▶ READY(revision N), candidate rejected
  ├─ different candidate validates ───────────────▶ READY(revision N+1)
  └─ confirmed Kite rejection of revision N ──────▶ AUTH_BLOCKED(revision N)

AUTH_BLOCKED(revision N)
  ├─ Calspread returns the same token ────────────▶ AUTH_BLOCKED
  ├─ Calspread is unavailable or unauthenticated ─▶ AUTH_BLOCKED
  ├─ different candidate fails validation ────────▶ AUTH_BLOCKED, candidate rejected
  └─ different candidate validates ───────────────▶ READY(revision N+1)
```

Only an explicit Kite authentication failure—such as a confirmed 401/403 or typed SDK
authentication error—may enter `AUTH_BLOCKED`. Feed age, repeated values, disconnects,
timeouts, rate limits, and general network errors are transport/data-health failures and do
not alter credential state.

Authentication-failure events carry the uplink's active `credential_revision`. `md-control`
ignores a delayed failure for an older revision, verifies that the reported revision is still
active, and performs an independent bounded Kite validation before globally blocking the
shared credential. A confirmed rejection blocks Kite calls from every WS/REST uplink using
that revision; a shard-specific or unconfirmed endpoint error remains a transport failure.

## Normal startup and restart

1. `md-control` loads the last validated credential cache.
2. A usable cached token allows market-data startup even if Calspread is temporarily
   unreachable. Source reachability is reported separately from token validity.
3. When synchronization is due, `md-control` retrieves Calspread's current token and compares
   it using secret-safe equality or a non-reversible fingerprint.
4. The same token does not increment revision, rewrite the runtime secret projection, or restart
   uplinks. Observation and validation timestamps may advance in control metadata, but they are
   not issuance or rotation timestamps.
5. A different token is validated with Kite before atomically replacing the active credential.
6. WS and REST uplinks reload only after the validated credential revision changes.

All WS shards and the REST warm standby use the same API key, token, and credential revision.
Sharding creates transport failure isolation, not separate broker sessions.

Credential revision is durable and monotonic across `md-control` restarts. On a validated
change, control first atomically commits the durable record, then atomically replaces the
runtime projection, announces only the opaque revision, and never publishes token material.
These are two ordered atomic file replacements, not one cross-volume transaction. Each uplink
reopens the projection, verifies the announced revision, builds its replacement client, and
acknowledges activation. Health reports
`active_credential_revision` per uplink so partial handoff is visible. A candidate that fails
validation never replaces the durable record or runtime projection and never advances revision.

The durable record is the source of truth. On every control startup, `md-control` reads it
before declaring readiness: `READY(N)` reconstructs projection N if needed, while
`AUTH_BLOCKED(N)`, `NO_TOKEN`, and `WAITING_FOR_ADMIN` remove any usable projection. Blocking
uses the reverse ordered protocol: stop and acknowledge all uplinks using N, durably commit
`AUTH_BLOCKED(N)`, then atomically remove or replace the runtime projection. A crash between
steps is repaired from durable state before any uplink can become ready.

## Feed recovery

Feed recovery is deliberately independent of authentication:

- restart the failed WS shard with the existing credential revision;
- keep unaffected WS shards running;
- let the tick bus select REST data where WS tokens are stale;
- restart a failed REST poller with the existing credential revision;
- perform no Calspread lookup merely because the feed is stale or disconnected.

A socket restart refreshes transport and subscriptions, not credentials.

## Genuine authentication rejection

When Kite explicitly rejects the active credential:

1. Stop Kite market-data calls from every uplink using the rejected revision. Keep tickbus,
   writer, stream, and control alive; the writer suppresses stale frames and retains open state.
2. Keep `md-control` live so health/status remains available.
3. Preserve the rejected credential record and mark its revision `AUTH_BLOCKED`; do not
   destructively delete it or repeatedly reinstall it.
4. Atomically remove the usable runtime secret projection or replace it with a tokenless blocked
   marker. Restarted uplinks must fail closed and cannot reopen the rejected credential.
5. Surface `operator_action_required: RELOGIN_CALSPREAD` without exposing token material.
6. On a bounded cadence, check whether Calspread now exposes a different token.
7. The same token leaves the system blocked. A different token must validate with Kite before
   it becomes the next revision and uplinks resume.

Recovery requires an administrator to log in again through Calspread. Automation cannot turn
the unchanged token into a new one.

## Persistence model

The target design separates credential lifetime from trading-date metadata:

```text
/var/lib/md-control/broker-credential.json  # private durable credential volume
_state/session-<date>.json      # risk-free rate and capture-day metadata
/run/mdsession/current.json     # minimal runtime projection; uplinks read-only
```

The durable credential record contains the secret access token plus non-secret metadata such as
`source`, monotonic `revision`, a non-reversible fingerprint, `retrieved_at`, `validated_at`,
and `status`. It is atomically replaced, mode `0600`, stored on a private `md-control` volume,
and excluded from errors and logs. The downloader does not know when Kite issued the token, so
`retrieved_at` must not be called `issued_at`, `rotated_at`, or treated as evidence of a token
change.

The current combined `session-<date>.json` is transitional. A new date may legitimately cache
the same token after validation; date rollover is not token refresh.

## Liveness and readiness

- Control liveness means the process, scheduler, and status API function. It stays healthy in
  `WAITING_FOR_ADMIN` and `AUTH_BLOCKED` so operators can see and resolve the condition.
- Market-data readiness requires `credential_status=READY` and the required data-plane services
  to have activated the current revision.
- Container supervision and Compose dependency checks use liveness, preventing missing admin
  login from creating a restart loop.
- Deployment/release gates use readiness. The compatibility `/health` endpoint may continue to
  represent aggregate readiness, while `/health/live` and `/health/ready` make the distinction
  explicit in the target API.

## Health and operator signals

Expose only redacted control-plane state:

- `credential_status`: `NO_TOKEN`, `WAITING_FOR_ADMIN`, `READY`, or `AUTH_BLOCKED`
- `credential_revision`
- `credential_source_status`: `UNKNOWN`, `AVAILABLE`, `UNAUTHENTICATED`, `UNREACHABLE`, or
  `INVALID_RESPONSE`
- `last_calspread_check_at`
- `last_validated_at`
- `auth_blocked_since` and redacted reason
- `operator_action_required`
- `observed_token_changes`

Do not report `token_refreshes`: this downloader never refreshes a token. Control-process
liveness and market-data readiness are separate—a live `md-control` may correctly report
unready uplinks while waiting for an administrator.

Token fingerprints are internal to `md-control` for secret-safe comparison and fencing. APIs,
metrics, and health messages expose only the opaque credential revision, never a token fragment
or fingerprint.

## Legacy implementation drift

The following current surfaces conflict with the target boundary and must not be copied into
new services:

- local Kite password/TOTP and request-token exchange;
- `KITE_API_SECRET`, `KITE_PASSWORD`, and login credentials in the downloader runtime;
- `refresh_broker_session` language and same-token refresh expectations;
- feed-stall recovery callbacks that contact Calspread;
- destructive session invalidation before a different token has been validated.

Until implementation catches up, operational documentation must identify these as legacy
behavior rather than presenting them as the desired fallback.
