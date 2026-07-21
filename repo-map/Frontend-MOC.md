---
title: Frontend-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/frontend]
up: "[[Home]]"
related: ["[[Live-Capture-MOC]]", "[[Operations-MOC]]", "[[Code-Map]]"]
---

# 🗺️ Frontend — MOC

> [!note] Next.js dashboard: capture monitor, option chain, stock board, login — all
> driven by the tagged-envelope WebSocket protocol + `/api/auth`.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[frontend]] | pages, components, and what each consumes | done |
| [[websocket-protocol]] | tagged envelope, topics, message types | done |

## Implemented in
- `frontend/app/{monitor,option-chain,stocks,login}/page.tsx` — the four pages
- `frontend/components/{OptionChainTable,SessionBadge,NavBar,ConnectionDot}.tsx`
- `frontend/lib/{wsTopicConnection,wsTypes,useTopic,api,config,numberFormat}.ts`
- Backend: `backend/app/ws/{protocol,routes}.py`, `capture/broadcaster.py`, `api/auth.py`
- Also: self-contained `/monitor` HTML served by FastAPI (`backend/app/static/monitor.html`)

Related: [[Live-Capture-MOC]] · [[Operations-MOC]]
