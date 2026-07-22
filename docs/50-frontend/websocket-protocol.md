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

Auth: the backend-issued opaque operator cookie is sent by the browser during the
WebSocket handshake. The backend validates both that HttpOnly cookie and the `Origin`
header. Tokens are never placed in query strings.

## Message types

| `type` | Payload summary |
|---|---|
| `MarketHeader` | Status-bar scalars: underlying, expiry, spot, spot_atm, atm, vix, timestamp, sequence (+ optional PCR/max-pain, no IV/Greeks) |
| `OptionGrid` | Full keyframe: `strikes[]` + per-side `GridBlock` (raw columns) |
| `OptionGridDelta` | Sparse patch: `changed_indices[]` + per-field arrays |
| `StockBoard` | Lightweight stock matrix snapshot (per stock: spot + up to 3 futures; live spread computed on read) |
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

## On-demand stock depth

`StockBoard` intentionally excludes depth so the 1 Hz board does not repeatedly send the
entire L5 matrix. Expanding a stock row calls
`GET /api/capture/stocks/{symbol}/depth`. The response contains `spot_depth` and a
`depth` array for each available futures leg. Each array has exactly five levels ordered
best-first. Prices are display-ready rupees; quantities and order counts remain integers.

```ts
interface DepthLevel {
  level: number;       // 1 through 5
  bid_price: number;
  bid_qty: number;
  bid_orders: number;
  ask_price: number;
  ask_qty: number;
  ask_orders: number;
}
```

## Keyframe / delta rules (kept)

- Full `OptionGrid` keyframe every N frames (algo_engine uses 30).
- `OptionGridDelta` for changed strike indices in between.
- Clients that miss a delta recover at the next keyframe (no resync handshake).

> Note: broadcast values are for **display**; they are independent of the on-disk
> integer-native format ([[bin-structure-spec]]). The backend converts paise→rupees and
> may compute Greeks for display only.
