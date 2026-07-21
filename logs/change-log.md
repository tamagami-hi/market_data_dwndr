---
title: Change Log
area: logs
type: log
status: living
tags: [area/logs, log/change, status/living]
up: "[[Logs-MOC]]"
related: ["[[decisions-and-open-questions]]", "[[progress-log]]"]
---

# Change Log

Design decisions and notable changes. Newest first.

---

## 2026-07-21 — Build started: Phase 0 scaffold + Phase 1 BIN codec landed
- Implemented the `backend/` FastAPI scaffold and the full integer-native BIN codec
  (`app/bin_codec/{layout,writer,reader,compress}.py`) exactly per
  [[bin-structure-spec]]. No format/schema decisions changed — the spec was
  implemented as-is (schema_version = 1).
- Implementation choices (non-schema): little-endian NumPy dtypes so `tobytes()` is the
  wire layout; raw `.bin` read via `mmap`, `.zst` via whole-stream decompress; reader
  returns raw integers (paise→rupees kept as an explicit separate step so round-trips
  stay bit-exact); verified raw removal after compression.
- Marked Phase 0 (minus deferred frontend) and Phase 1 batches done in [[build-guide]].

## 2026-07-21 — Branch setup: main (baseline/default) + ai-dev/made (working)
- Created `main` as the stable/default baseline and `ai-dev/made` as the active
  development branch. Phase work lands on `ai-dev/made` and PRs into `main`.
- Documented the git/PR workflow in [[next-session-handoff]].

## 2026-07-21 — Gap docs + build guide added; vault completed
- Added [[build-guide]] (phase/batch checklist with DoD gates), the `60-operations`
  domain ([[operations-runbook]], [[config-and-env]], [[session-state]],
  [[failure-modes]], [[data-retention]]), [[testing-strategy]], and [[reconstruction]].
- New MOCs [[Operations-MOC]] + [[Quality-MOC]]; Home/Overview/Data-Storage/Tags updated.
- Knowledge base is now complete; ready to branch + push.

## 2026-07-21 — Knowledge base reorganized into an Obsidian vault
- Moved 17 flat docs into domain folders under `docs/`; added YAML frontmatter,
  wikilinks, and tags. Created `logs/` and `repo-map/` (MOCs). See [[Home]].

## 2026-07-21 — Depth levels locked: indices L1, stocks L5
- Rationale + sources in [[depth-level-research]]. Affects [[bin-structure-spec]],
  [[stocks-capture]].

## 2026-07-21 — Numeric types → integer-native (lossless)
- Prices as `i64` paise, quantities/OI `u64`, orders `u32`; bond yield the only `f64`.
- Replaces the earlier `f64` plan. See [[lossless-and-precision]], [[bin-structure-spec]].

## 2026-07-21 — Greeks/IV not stored; bond yield stored in header
- Greeks reconstructed on read (Black-Scholes + stored 10-yr yield). Bond yield entered
  manually at login. Means files are **not** the byte-identical `algo_engine` structs.
  See [[bin-format]].

## 2026-07-21 — Format engine: struct + NumPy + zstandard
- Dropped msgpack/bincode-lib/Parquet ideas. Historical uses the same `.bin` format.
  See [[tech-stack-and-efficiency]].

## 2026-07-21 — Scope, cadence, universe
- 1 Hz capture; indices NIFTY/BANKNIFTY/FINNIFTY/SENSEX (no MIDCPNIFTY/BANKEX); full
  F&O stock board; KiteTicker; `.env` api key+secret + daily login. See
  [[decisions-and-open-questions]].
