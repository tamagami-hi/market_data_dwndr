---
title: Next-Session Handoff
area: logs
type: reference
status: living
tags: [area/logs, type/reference, status/living]
up: "[[Logs-MOC]]"
related: ["[[build-guide]]", "[[bin-structure-spec]]", "[[decisions-and-open-questions]]", "[[Home]]"]
---

# Next-Session Handoff

Paste the prompt below into a fresh session to resume. It points the agent at the
vault and kicks off **Phase 1 (BIN codec)** per [[build-guide]].

```text
I'm continuing the `market_data_dwndr` project — a Python + Next.js market-data
downloader for Zerodha Kite (capture only, no trading). The full design is already
documented as an Obsidian vault in the repo. Work on branch `ai-dev/made` (never push
to main).

FIRST, read the knowledge base before writing any code:
- repo-map/Home.md            (start here — Map of Content)
- docs/00-overview/overview-and-scope.md
- docs/00-overview/build-guide.md          (the phase/batch plan with DoD gates)
- docs/20-data-and-storage/bin-structure-spec.md   (authoritative byte format)
- docs/90-decisions/decisions-and-open-questions.md (locked decisions)
- docs/10-architecture/concurrency-and-gil.md and tech-stack-and-efficiency.md

LOCKED DESIGN (do not re-litigate):
- Live capture over Kite WebSocket via KiteTicker, 1 Hz snapshots, push-based only.
- Indices: NIFTY, BANKNIFTY, FINNIFTY, SENSEX; ATM±50 = 101 strikes; depth L1.
- Stocks: full F&O board (spot + 3 nearest futures) as a matrix; depth L5.
- Storage: our own integer-native BIN format (prices i64 paise, qty/OI u64, orders
  u32), LE fixed-width bincode-style framing `[u32 len][payload]`, header-once then
  1 Hz frames, whole-file zstd level 17 at end of day.
- Greeks/IV are NOT stored — reconstructed on read from raw + the 10-yr bond yield
  stored in each file header (bond yield entered manually at morning login).
- Folders: MARKET_DATA/INDICES/<INDEX>/<date>.bin, MARKET_DATA/STOCKS/<date>.bin,
  and INDICES_HIS/ , STOCKS_HIS/ for historical.
- Config via .env (KITE_API_KEY, KITE_API_SECRET, MARKET_DATA_PATH); daily login
  exchanges request_token -> access_token (see docs/60-operations/).
- Tooling: FastAPI backend; struct + NumPy + zstandard for the codec (NO bincode lib,
  NO msgpack, NO Parquet); Next.js frontend (reused option-chain UI + a Capture
  Monitor dashboard).

TASK THIS SESSION:
1. Phase 0 scaffolding if not present: backend/ (pyproject.toml, app/main.py FastAPI
   with /health, app/config.py pydantic-settings), .env.example. (frontend/ can wait.)
2. Phase 1 — BIN codec, implemented EXACTLY per docs/20-data-and-storage/
   bin-structure-spec.md:
     backend/app/bin_codec/layout.py   (field order + dtypes, enum tags, primitives)
     backend/app/bin_codec/writer.py   (framing, header-once, IndexFrame + StockFrame)
     backend/app/bin_codec/reader.py   (scan -> ts->offset index, binary search, ranges)
     backend/app/bin_codec/compress.py (whole-file zstd L17, transparent .zst read)
3. Tests per docs/70-quality/testing-strategy.md — the Phase 1 Definition-of-Done
   gates MUST pass: round-trip identical integer arrays, byte-level header check,
   compress -> re-index -> identical.

Update logs/progress-log.md (and logs/change-log.md if a decision changes) as you go.
Stay on branch ai-dev/made. Follow the DoD gates in build-guide.md before advancing.

WORKFLOW (git):
- Work on `ai-dev/made`; never commit/push to `main` directly.
- Commit in scoped batches as you complete build-guide tasks.
- Push `ai-dev/made` to the remote (use the GitHub push tool, not raw `git push`).
- When Phase 1's Definition-of-Done gates pass, open/update a Pull Request from
  `ai-dev/made` into `main` for review.
```

## Branch workflow

- **`main`** = stable/default baseline. **`ai-dev/made`** = active development.
- Each phase's work lands on `ai-dev/made` and is reviewed via a PR into `main`.
