---
title: Historical-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/historical]
up: "[[Home]]"
related: ["[[Data-Storage-MOC]]", "[[option-chain-selection]]", "[[Code-Map]]"]
---

# 🗺️ Historical — MOC

> [!note] Backfill OHLC+OI candles into the same `.bin` format as live, resumable.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[historical-data]] | Kite historical API, windowing, rate limits, resume | done |

## Implemented in
- `backend/app/historical/intervals.py` — interval policy table
- `backend/app/historical/{windows,request}.py` — chunking + request validation
- `backend/app/historical/{limiter,client}.py` — token bucket + fetch/retry
- `backend/app/historical/assembly.py` — candle → frame (INDICES_HIS / STOCKS_HIS)
- `backend/app/historical/jobs.py` — checkpoints, resume, progress
- Tests: `test_historical_core`, `test_historical_assembly`, `test_historical_jobs`

Related: [[Data-Storage-MOC]] · [[option-chain-selection]]
