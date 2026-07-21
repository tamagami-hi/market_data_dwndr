# market_data_dwndr — backend

Python/FastAPI backend for the Zerodha Kite market-data downloader (capture only, no
trading). See the knowledge base under [`../docs`](../docs) and the phase/batch plan in
[`../docs/00-overview/build-guide.md`](../docs/00-overview/build-guide.md).

## Setup

```bash
cd backend
uv venv                      # or: python -m venv .venv
uv pip install -e ".[dev]"   # runtime + dev deps
cp .env.example .env         # then fill in KITE_API_KEY / SECRET / MARKET_DATA_PATH
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
# GET http://localhost:8000/health -> {"status": "ok", ...}
```

## Test

```bash
pytest
```

## Layout

```
app/
  main.py        FastAPI app + /health (Phase 0)
  config.py      pydantic-settings (.env)
  bin_codec/     BIN read/write/compress (Phase 1)   <- foundation
  kite/          auth, instruments, ticker, historical (Phase 2/3/6)
  chain/         option-chain filter/assembler/table (Phase 2/3)
  stocks/        F&O board discovery + matrix (Phase 2/3)
  capture/       1 Hz snapshot engine + monitor (Phase 3/4)
  historical/    backfill jobs (Phase 6)
  ws/            tagged-envelope WebSocket protocol (Phase 4)
```
