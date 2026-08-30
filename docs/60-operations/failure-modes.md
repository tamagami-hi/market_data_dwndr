---
title: Failure Modes & Recovery
area: operations
type: spec
status: locked
tags: [area/operations, type/spec, status/locked]
up: "[[Operations-MOC]]"
related: ["[[operations-runbook]]", "[[session-state]]", "[[live-data-pipeline]]", "[[bin-structure-spec]]"]
---

# Failure Modes & Recovery

How the capture service behaves when things go wrong. The guiding rule: **never
corrupt an existing `.bin`; prefer pausing/retrying over crashing.**

> **Implementation status:** Rows involving credential revisions, same-token fencing, and
> `AUTH_BLOCKED` describe the locked target architecture, not current behavior. Current code
> still contains legacy direct-login, invalidation, and refresh-named paths.

| Failure | Detection | Response |
|---|---|---|
| **WS disconnect / stall** | no message or stale content beyond the configured threshold | current monolith exits/restarts the process; target restarts only the affected transport with the **same credential revision** and never calls Calspread or mutates auth state ([[live-data-pipeline]]) |
| **Explicit Kite authentication rejection** *(target)* | revision-fenced typed auth report independently confirmed by `md-control` | stop Kite calls from every uplink using the rejected revision; keep writer/tickbus/control live, block the runtime projection, preserve the durable record, and require administrator relogin through Calspread; the same token remains blocked, while a different validated token advances revision |
| **Calspread unavailable** | timeout, network, malformed response | keep using a cached token that Kite has validated and report source degradation; if no usable token exists, fail closed and wait—never fall back to downloader-owned broker login |
| **Disk full / write error** | `write()`/`flush()` error | stop the affected writer, log + alert on Capture Monitor, keep other writers running; retry after space frees; the last good frame is already flushed |
| **Truncated frame** (crash mid-write) | reader: declared `len` overruns EOF | reader stops at the last **complete** frame and ignores the trailing partial (matches `algo_engine` reader behavior) |
| **Corrupt `.zst`** | zstd decode error | fall back to a raw `.bin` if present; otherwise flag the file; never delete raw until compression verified |
| **Unmatched ticks** | token not in map | increment an `unmatched` counter (surfaced on the monitor); ignore — not fatal |
| **Process restart mid-day** | startup finds today's session + files | reuse token + risk-free rate, **append** to today's files (header only if empty) ([[session-state]]) |
| **Clock skew** | — | timestamps come from tick receive time (and optionally `exchange_timestamp`); files keyed by IST trading date |

## Invariants

- Header is written **only** when a file is empty → restarts never duplicate it.
- Compression **verifies** the `.zst` before removing the raw `.bin`.
- Writers flush after each 1 Hz frame, so at most the in-flight frame is at risk.
- A single stream's failure is **isolated** (per-file writer) and never stalls others.
- Credential retrieval is idempotent: observing the same Calspread token never counts as a
  refresh, changes credential revision, or restarts an uplink.

See [[broker-session-authentication]] for the authority boundary and operator recovery state.
