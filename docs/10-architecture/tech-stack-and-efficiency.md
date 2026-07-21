---
title: Tech Stack & Efficiency
area: architecture
type: spec
status: locked
tags: [area/architecture, type/spec, status/locked]
up: "[[Architecture-MOC]]"
related: ["[[live-capture-performance]]", "[[bin-structure-spec]]", "[[live-data-pipeline]]"]
---

# Tech Stack & Efficiency

## Backend stack

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Requested |
| Web framework | **FastAPI** + Uvicorn | Async, native WebSocket + REST, Pydantic models |
| Kite WebSocket | **`kiteconnect` (KiteTicker)** | Official SDK decodes the binary packet (incl. 5-level depth) & handles reconnect |
| Kite REST | `kiteconnect` / `httpx` | Instruments, LTP, historical |
| BIN encoding | **`struct` + NumPy** (custom codec) | Our own integer-native schema ([[bin-structure-spec]]); `numpy.tobytes` LE layout is the wire layout — exact, zero-parse |
| Compression | **`zstandard`** (level 17) | Same algorithm/level as `algo_engine` |
| Numeric arrays | **NumPy** (integer dtypes) | Columnar tables, fast in-place updates, exact `i64`/`u64` |
| Rate limiting | `aiolimiter` or custom token bucket | Historical download throttling |
| Config | Pydantic Settings + `.env` | Typed; `KITE_API_KEY`/`KITE_API_SECRET`/`MARKET_DATA_PATH` |

> **No `msgpack`, no bincode library, no Parquet.** We define the format ourselves
> ([[bin-structure-spec]]); `struct` + NumPy is exact and fastest, and historical uses
> the **same `.bin` format** (not Parquet) so one toolchain serves everything.

## Frontend stack

Next.js 16 / React 19 / Tailwind 4, reusing `algo_engine` components + a new Capture
Monitor page ([[frontend]]).

## "Parallel computation and efficiency" — the honest model

Python has a **GIL**, so "parallel" means different things per workload:

- **I/O-bound (most of this app) → `asyncio`.** WebSocket ingest, REST historical
  fetches, and broadcasting. Historical downloads: many concurrent tasks bounded by a
  semaphore, sharing one rate limiter.
- **Blocking file I/O → dedicated thread.** The `.bin` writer runs in a thread fed by
  a queue; `write()` releases the GIL. At 1 Hz this is nearly idle.
- **CPU-bound (zstd) → thread/process pool at EOD.** The `zstandard` C extension
  releases the GIL, so per-file compression parallelizes off the hot path.
- **No heavy per-tick math.** We don't compute Greeks at capture, so per tick is just
  NumPy integer writes. Optimize for I/O concurrency + steady 1 Hz writes.

## Efficiency tactics

- **Integer columnar arrays** (NumPy `i64`/`u64`) — cache-friendly, exact, compress well.
- **Token → index map** for O(1) tick application.
- **Strikes stored once in the header** (fixed ATM window for the day).
- **1 Hz cadence** bounds frame count and gives wall-clock-aligned samples.
- **zstd L17 at EOD**, off the hot path.

See [[live-capture-performance]] for concrete sizing at 1 Hz (indices L1 / stocks L5).
