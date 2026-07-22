---
title: Decisions & Open Questions
area: decisions
type: decision
status: locked
tags: [area/decisions, type/decision, status/locked]
up: "[[Decisions-MOC]]"
related: ["[[overview-and-scope]]", "[[bin-structure-spec]]", "[[implementation-plan]]", "[[depth-level-research]]"]
---

# Decisions & Open Questions

## Resolved decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Storage approach | `algo_engine`-style framing (`[u32 LE len][payload]`, header-once, frame-by-frame, whole-file zstd L17) with **our own schema** — not byte-identical to algo_engine. ([[bin-format]], [[bin-structure-spec]]) |
| 2 | Numeric types | **Integer-native / lossless:** prices `i64` paise, quantities/OI/volume `u64`, order counts `u32`, timestamps `u64`. `risk_free_rate` is the only `f64`. ([[lossless-and-precision]]) |
| 3 | Bond yield | **Stored in every file header.** The 10-year government bond yield is manually confirmed, reusable the next Monday–Friday market day, and mandatory to update on the third market day. Weekends do not count. Enables Greek reconstruction. ([[bin-format]]) |
| 4 | Greeks / IV | **Not stored at all** — reconstructed on read via Black-Scholes + header bond yield. ([[bin-format]]) |
| 5 | Raw only | Store raw API fields; drop all computable (`change`, `change_in_oi`, IV, Greeks, spreads, summary stats). No raw loss. ([[lossless-and-precision]]) |
| 6 | Depth — indices | **L1** (top of book) for the option chain. ([[depth-level-research]]) |
| 7 | Depth — stocks | **L5** (top-5 order book each side) for spot + each future. ([[depth-level-research]], [[stocks-capture]]) |
| 8 | Cadence | **1 Hz** — one snapshot/second, last-value-wins. ([[live-capture-performance]]) |
| 9 | Index universe | **NIFTY, BANKNIFTY, FINNIFTY, SENSEX.** MIDCPNIFTY and BANKEX **excluded**. ([[option-chain-selection]]) |
| 10 | Stock universe | **Full F&O stock board** (CalSpread discovery). ([[stocks-capture]]) |
| 11 | Storage layout | `INDICES/<INDEX>/<date>.bin`, `STOCKS/<date>.bin` (all stocks, matrix); historical under `INDICES_HIS/` & `STOCKS_HIS/`. ([[storage-layout]]) |
| 12 | Historical | Same BIN format as live. ([[historical-data]]) |
| 13 | VIX | Kept (raw). |
| 14 | Compression | zstd level 17 at end of day. ([[bin-format]]) |
| 15 | WebSocket lib | **KiteTicker** (decodes 5-level depth + reconnect), bridged into an asyncio queue. ([[tech-stack-and-efficiency]]) |
| 16 | Auth | The backend polls and validates the shared Kite token during 08:30–09:00 IST; env-seeded credentials + user-entered TOTP remain the fallback. Bond-yield freshness gates capture. ([[config-and-env]]) |
| 17 | BIN tooling | Python `struct` + NumPy custom codec + `zstandard` (no bincode lib, no msgpack, no Parquet). ([[tech-stack-and-efficiency]]) |
| 18 | Frontend | Reused option-chain/historical UI + a new **Capture Monitor** dashboard. ([[frontend]]) |
| 19 | Knowledge base | `docs/` (domain folders) + `logs/` + `repo-map/` (Obsidian vault). |
| 20 | Build order | Live downloader first (BIN codec → discovery → live capture → monitor), then historical. ([[implementation-plan]]) |

## Still open (minor, non-blocking)

### A. Non-Greek header analytics
Whether to show cheap aggregations (PCR-OI, max-pain, live spread) in the UI header.
These are display-only (never persisted). Recommendation: include a minimal set.

### B. Capture Monitor metric set
Exact fields on the `CaptureStatus` message ([[websocket-protocol]]). Current draft:
connected, last_tick_ms, frames_written, file_bytes, heartbeat_ok, unmatched, plus a
global tokens/fps/disk panel. Refine during Phase 4.

### C. Exchange timestamp (optional fidelity)
Whether to also store Kite's `exchange_timestamp` per snapshot ([[lossless-and-precision]]).
Recommendation: include it (cheap, useful for exact market timing).

Nothing here blocks the build.
