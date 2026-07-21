---
title: Testing Strategy
area: quality
type: spec
status: locked
tags: [area/quality, type/spec, status/locked]
up: "[[Quality-MOC]]"
related: ["[[build-guide]]", "[[bin-structure-spec]]", "[[option-chain-selection]]", "[[historical-data]]", "[[reconstruction]]"]
---

# Testing Strategy

Tests map to the Definition-of-Done gates in [[build-guide]]. Priority: the **BIN
codec** (everything depends on it) and the **chain filter** (correctness of what we
capture).

## Unit tests

- **BIN codec round-trip** (Phase 1): write `IndexFrame`/`StockFrame` with known
  integer arrays → read back → assert **bit-identical** arrays and scalars. Include
  edge cases: empty depth, missing futures slot (`0` sentinel), max `u64`, negative
  paise never occurs (guard).
- **Byte-level layout**: build a header by hand and assert the exact byte offsets /
  values (primitives, `u32` tag, `u64` lengths, `i64` paise) per [[bin-structure-spec]].
- **Chain filter** ([[option-chain-selection]]): on fixed instrument fixtures, assert
  exactly the ATM ± 50 window; ATM rounding per `step`; nearest-ATM fallback; empty
  strike list → error.
- **Board discovery** ([[stocks-capture]]): NFO FUT names matched to NSE EQ; indices
  excluded; 3-nearest-futures ordering.
- **Reconstruction** ([[reconstruction]]): Greeks from a known frame + bond yield match
  a reference within tolerance; spread = `mid.ltp − current.ltp`.

## Integration tests

- **Compress/re-index** (Phase 1): `.bin` → `.bin.zst` → reader re-index → identical
  frame timestamps/values.
- **Capture loop** (Phase 3): feed a recorded/synthetic tick stream → assert ~1
  frame/s per file, correct token→index routing, `unmatched` behavior, L1 (index) /
  L5 (stock) shapes.
- **Reconnect** (Phase 3): drop the socket → assert re-subscribe + no corruption + no
  duplicate header.
- **Historical resume** (Phase 6): interrupt a job mid-run → resume from `_state`
  checkpoint → **no duplicate rows**, contiguous timestamps.
- **Restart mid-day** (Phase 5): kill + restart → appends to today's files, reuses
  token + bond yield ([[session-state]]).

## Acceptance / smoke

- End-to-end dry run against a replayed session: files created under the right paths
  ([[storage-layout]]), Capture Monitor reflects reality, EOD compression runs.

## Tooling & conventions

- `pytest` for backend; fixtures for instrument dumps and synthetic ticks.
- Deterministic tests: no live network in unit tests (mock Kite); a separate,
  opt-in "live" marker for manual verification.
- Frontend: type-check + a minimal render test for the Capture Monitor + option chain.
- CI (optional): run `pytest` + lint on push.
