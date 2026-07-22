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

| Failure | Detection | Response |
|---|---|---|
| **WS disconnect / stall** | no message for ~30 s | reconnect with exp backoff (base 5 s → max 300 s), circuit breaker after ~20 attempts; re-subscribe the same tokens; tables/writers persist ([[live-data-pipeline]]) |
| **Mid-day auth expiry** | Kite auth error on WS/REST | cancel the live engine, flush writers, invalidate only the exact rejected persisted token, and let daily automation poll the existing HTTPS broker at the bounded interval; a validated replacement restarts capture on the same files |
| **Disk full / write error** | `write()`/`flush()` error | stop the affected writer, log + alert on Capture Monitor, keep other writers running; retry after space frees; the last good frame is already flushed |
| **Truncated frame** (crash mid-write) | reader: declared `len` overruns EOF | reader stops at the last **complete** frame and ignores the trailing partial (matches `algo_engine` reader behavior) |
| **Corrupt `.zst`** | zstd decode error | fall back to a raw `.bin` if present; otherwise flag the file; never delete raw until compression verified |
| **Unmatched ticks** | token not in map | increment an `unmatched` counter (surfaced on the monitor); ignore — not fatal |
| **Process restart mid-day** | startup finds today's session + files | reuse token + bond yield, **append** to today's files (header only if empty) ([[session-state]]) |
| **Clock skew** | — | timestamps come from tick receive time (and optionally `exchange_timestamp`); files keyed by IST trading date |

## Invariants

- Header is written **only** when a file is empty → restarts never duplicate it.
- Compression **verifies** the `.zst` before removing the raw `.bin`.
- Writers flush after each 1 Hz frame, so at most the in-flight frame is at risk.
- A single stream's failure is **isolated** (per-file writer) and never stalls others.
