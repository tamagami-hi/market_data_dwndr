---
title: Frontend
area: frontend
type: spec
status: locked
tags: [area/frontend, type/spec, status/locked]
up: "[[Frontend-MOC]]"
related: ["[[websocket-protocol]]", "[[live-data-pipeline]]", "[[historical-data]]"]
---

# Frontend

A Next.js/TypeScript app reusing `algo_engine/frontend_stack` components, plus a new
**Capture Monitor** dashboard that visualizes what is being saved to disk. Everything
tied to execution, strategies, and risk is dropped.

## Stack (from algo_engine)

- Next.js 16, React 19, TypeScript 5
- Tailwind CSS 4
- `@tanstack/react-virtual` (virtualized tables)
- `recharts` (charts), `lucide-react` (icons), `react-hot-toast` (toasts)

## Pages

- **`/monitor` — Capture Monitor (new, the "data saving" view).** Per-underlying status
  cards + global panels (see below).
- `/option-chain` — live option-chain table + status panel (reused).
- `/stocks` — live stock board (spot + 3 futures, live spread computed on read).
- `/historical` — download form, progress, dataset catalog (reused).

## Capture Monitor (interactive data-saving dashboard)

Driven by a `capture-status` WS topic ([[websocket-protocol]]). Shows:

- **Per-underlying cards** (each index + the stock file): WS connected?, last tick
  time, frames written today, current file size, 1 Hz heartbeat (green if a frame was
  written in the last ~2 s), unmatched-tick counter.
- **Global panel:** total tokens subscribed, frames/sec, `MARKET_DATA` disk usage,
  today's file list with sizes, EOD-compression status.
- **Session/log stream:** connection events, reconnects, errors (from the `session`
  topic).

## Components to reuse

| Component | Reuse | Change |
|---|---|---|
| `OptionChainTable.tsx` | ✅ | Show OI, Chg OI, Vol, Bid, Ask, LTP, Chg; Greeks/IV computed on read (optional columns) |
| `StrikeMarkers.tsx` | ✅ | As-is (ATM / spot markers) |
| `StatusPanel.tsx` | ✅ | Trim Greek/IV-dependent metrics |
| `HistoricalDownloadPanel/Progress/Catalog.tsx` | ✅ | Map to historical API |
| `lib/wsTopicConnection.ts` | ✅ | As-is (topic WS with reconnect) |
| `hooks/useMarketStore.ts` | ✅ | Drop stored-Greek handling; compute on read if displayed |
| `hooks/useHistoricalJobs.ts` | ✅ | As-is |
| Execution / strategy / backtest / risk components | ❌ | Not ported |

## Option-chain columns

CALLS and PUTS show **OI, Chg OI, Volume, Bid, Ask, LTP, Chg**. Greeks/IV are **not in
the feed**; if displayed they are computed client- or server-side from raw + the day's
bond yield ([[bin-format]]). ATM / spot-ATM row highlighting stays.

## Data flow

- The market store consumes the tagged WS envelope `{ type, payload }` from
  `/ws/market-data` ([[websocket-protocol]]).
- `OptionGrid` = full keyframe; `OptionGridDelta` = sparse patch on changed strikes;
  keyframe every N frames (a pure transport optimization).
- Capture Monitor consumes `/ws/capture-status`.

## What we delete

Strategy building, live/backtest execution, positions, P&L, risk management, RBI-rate,
and settings that only served those flows.
