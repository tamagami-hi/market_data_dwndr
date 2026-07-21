---
title: Architecture-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/architecture]
up: "[[Home]]"
related: ["[[Data-Storage-MOC]]", "[[Live-Capture-MOC]]", "[[Code-Map]]"]
---

# 🗺️ Architecture — MOC

> [!note] The stack and how we parallelize I/O around the GIL.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[tech-stack-and-efficiency]] | FastAPI, KiteTicker, struct+NumPy+zstandard | locked |
| [[concurrency-and-gil]] | thread-per-file writers + async ingest around the GIL | locked |

## Implemented in
- `backend/app/capture/engine.py` — asyncio ingest + 1 Hz snapshot loop
- `backend/app/capture/writer_thread.py` — GIL-releasing writer thread per file
- `backend/app/kite/ticker.py` — KiteTicker → `asyncio.Queue` bridge

Related: [[Data-Storage-MOC]] · [[Live-Capture-MOC]]
