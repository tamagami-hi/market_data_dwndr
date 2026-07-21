---
title: Data Retention
area: operations
type: spec
status: locked
tags: [area/operations, type/spec, status/locked]
up: "[[Operations-MOC]]"
related: ["[[storage-layout]]", "[[operations-runbook]]", "[[live-capture-performance]]"]
---

# Data Retention

## Budget context

At 1 Hz: ~2.2 GB/day raw for 4 indices + ~5.8 GB/day raw for the stock matrix (L5) ⇒
**~8 GB/day raw → ~1.5–2.5 GB/day compressed** ([[live-capture-performance]]). Stated
budget: ~1 GB/day sustainable for ~2 years, with more storage planned.

## Policy (default)

- **Raw `.bin`:** transient — exists only during the session; removed after the EOD
  `.zst` is written and verified. Never retained long-term.
- **Compressed `.bin.zst`:** the durable artifact — **kept indefinitely** by default
  (this is the point of the project).
- **Instrument archives (`_instruments/`):** kept indefinitely (needed to reconstruct
  past boards / expired tokens, [[storage-layout]]).
- **Session state (`_state/`):** kept; small.

## Configurable knobs (future)

- Optional **cold-tier move** of `.zst` older than N months to cheaper storage.
- Optional **prune** of raw scratch on startup (in case a crash left an uncompressed
  `.bin` from a prior day → compress-then-remove).
- No lossy downsampling — retention is about *where* data lives, never dropping fields
  (raw-only + integer-native is already minimal, [[lossless-and-precision]]).

## Integrity

- Verify `.zst` decodes + re-indexes before deleting the raw `.bin`.
- Periodic (optional) spot-check: decompress a random day, confirm frame count and
  timestamp monotonicity.
