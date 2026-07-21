---
title: WebSocket Protocol
area: frontend
type: spec
status: locked
tags: [area/frontend, type/spec, status/locked]
up: "[[Frontend-MOC]]"
related: ["[[frontend]]", "[[live-data-pipeline]]", "[[historical-data]]"]
---

# WebSocket Protocol (Backend → Frontend)

The frontend expects a tagged-JSON envelope, identical in shape to `algo_engine`:

```json
{ "type": "OptionGrid", "payload": { ... } }
```
Keeping this contract means the reused `wsTopicConnection.ts` + `useMarketStore.ts`
work with minimal edits.

## Topics (`/ws/{topic}`)

| Topic | Purpose | Keep? |
|---|---|---|
| `market-data` | Live option-chain frames | ✅ |
| `stocks` | Live stock board frames (spot + futures) | ✅ (new) |
| `capture-status` | Data-saving telemetry for the Capture Monitor | ✅ (new) |
| `session` | Status phase, logs, heartbeat | ✅ (trimmed) |
| `historical-jobs` | Historical download progress | ✅ |
| `backtest` / `execution` | replay / execution | ❌ (out of scope) |

Auth: token as `?token=` query param (same as algo_engine).

## Message types

| `type` | Payload summary |
|---|---|
| `MarketHeader` | Status-bar scalars: underlying, expiry, spot, spot_atm, atm, vix, timestamp, sequence (+ optional PCR/max-pain, no IV/Greeks) |
| `OptionGrid` | Full keyframe: `strikes[]` + per-side `GridBlock` (raw columns) |
| `OptionGridDelta` | Sparse patch: `changed_indices[]` + per-field arrays |
| `StockBoard` | Stock matrix snapshot (per stock: spot + 3 futures raw fields; live spread computed on read) |
| `CaptureStatus` | Per-underlying: connected, last_tick_ms, frames_written, file_bytes, heartbeat_ok, unmatched; global: tokens, fps, disk_bytes |
| `Heartbeat` | `{ ts }` liveness |
| `SessionStatus` | Phase string + connection diagnostics |
| `Log` | Log line string |
| `HistoricalJobUpdate` | Historical job progress state |

## `GridBlock` (downloader)

```ts
interface GridBlock {
  ltp: number[];      // rupees (paise/100 on the wire→UI)
  oi: number[];
  volume: number[];
  bid: number[];
  bid_qty: number[];
  ask: number[];
  ask_qty: number[];
  oi_day_high: number[];
  oi_day_low: number[];
  // change, change_in_oi, iv, delta, gamma, theta, vega, rho: NOT in the feed —
  // computed on read for display if needed (raw + bond yield).
}
```

## Keyframe / delta rules (kept)

- Full `OptionGrid` keyframe every N frames (algo_engine uses 30).
- `OptionGridDelta` for changed strike indices in between.
- Clients that miss a delta recover at the next keyframe (no resync handshake).

> Note: broadcast values are for **display**; they are independent of the on-disk
> integer-native format ([[bin-structure-spec]]). The backend converts paise→rupees and
> may compute Greeks for display only.
