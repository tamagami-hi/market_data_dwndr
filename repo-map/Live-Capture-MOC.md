---
title: Live-Capture-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/live-capture]
up: "[[Home]]"
related: ["[[Data-Storage-MOC]]", "[[Frontend-MOC]]", "[[Code-Map]]"]
---

# 🗺️ Live Capture — MOC

> [!note] From token discovery to a 1 Hz snapshot on disk — indices (L1) and stocks (L5).

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[option-chain-selection]] | ATM ± 50 window, guards, per-index config | done |
| [[live-data-pipeline]] | bootstrap → subscribe → ingest → 1 Hz snapshot → persist | done |
| [[stocks-capture]] | CalSpread board, stock matrix (L5) | done |
| [[live-capture-performance]] | sizing & architecture at 1 Hz | locked |

## Implemented in
- `backend/app/chain/{config,filter,assembler,table}.py` — index chain (L1)
- `backend/app/stocks/{board,matrix}.py` — F&O board (L5)
- `backend/app/kite/{ticks,ticker}.py` — tick decode + WebSocket bridge
- `backend/app/capture/{engine,writer_thread,reconnect,monitor}.py` — the loop
- Tests: `test_chain`, `test_board`, `test_ticks`, `test_ticker`, `test_table_matrix`, `test_capture`

Related: [[Data-Storage-MOC]] · [[Frontend-MOC]] · [[Operations-MOC]]
