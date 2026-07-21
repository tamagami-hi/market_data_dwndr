---
title: Concurrency & the GIL (saving data)
area: architecture
type: spec
status: locked
tags: [area/architecture, type/spec, status/locked]
up: "[[Architecture-MOC]]"
related: ["[[tech-stack-and-efficiency]]", "[[live-data-pipeline]]", "[[live-capture-performance]]", "[[bin-format]]"]
---

# Concurrency & the GIL (how we save data)

## The GIL, precisely

CPython's **Global Interpreter Lock** lets only **one thread execute Python bytecode
at a time**. So *pure-Python* CPU work does **not** run in parallel across threads. But
the GIL is **released** during:

- **Blocking I/O** — socket `recv`, file `write`/`flush`, `os` calls.
- **C-extension heavy work that opts out** — **NumPy** on sizable arrays,
  **`zstandard`** compression, etc.

Our saving path is almost entirely those two categories, so **we don't fight the GIL —
we ride the parts that release it.**

## Why saving is not GIL-bound here

At **1 Hz** the per-second work is tiny (a few small integer arrays per file, ~24.5 KB
index / ~0.26 MB stocks — [[live-capture-performance]]). Even single-threaded Python
would keep up with room to spare. Threading below is for **isolation and never
blocking the event loop**, not raw throughput.

## The concurrency model

```
KiteTicker thread(s)                 asyncio event loop (1 thread)              writer thread(s)
──────────────────                   ────────────────────────────              ────────────────
on_ticks(callback) ──call_soon_ ──►  apply ticks to NumPy tables (cheap)
        threadsafe                   1 Hz timer: snapshot tables ──► queue ──►  serialize (numpy.tobytes)
   (GIL released on socket recv)     (GIL held briefly, trivial)               write()/flush()  (GIL released)
                                                                                └─ per file: 4 indices + 1 stock matrix
                                     ── at market close ──►  EOD compress pool: zstd L17 per file (GIL released)
```

1. **Ingest** — KiteTicker runs its own thread(s); its callback hands ticks to the loop
   via `loop.call_soon_threadsafe` into an `asyncio.Queue`. Socket recv releases the GIL.
2. **Apply** — on the event loop: O(1) NumPy integer writes at `token→index`. Cheap; no
   need to parallelize.
3. **Snapshot (1 Hz)** — an asyncio timer copies each table and enqueues a frame to the
   writer(s).
4. **Serialize + write** — offloaded to a **writer thread** (`asyncio.to_thread` or a
   dedicated `threading.Thread` draining a `queue.Queue`). Both hot operations —
   `numpy.tobytes` (a C memcpy) and `file.write()` — **release the GIL**, so the writer
   runs concurrently with ongoing ingest/apply without contention. One writer thread is
   plenty at 1 Hz; an optional **thread-per-file** (5 live files) adds isolation so a
   slow disk op on one file never delays another.

## The one genuinely CPU-bound step: EOD zstd L17

Compressing a whole day's file at level 17 is CPU-heavy — but it runs **once at end of
day, off the hot path**, and `zstandard` is a **C extension that releases the GIL**.
So:

- **Default: `ThreadPoolExecutor`.** Because zstd releases the GIL, compressing the
  several daily files runs **truly in parallel** across cores from threads — simple, no
  IPC.
- **Fallback: `ProcessPoolExecutor`.** If we ever add *pure-Python* CPU-bound batch work
  (unlikely on the capture path), processes sidestep the GIL entirely. We'd pass file
  **paths** (cheap to pickle), not data. This is the tool for heavy reconstruction jobs
  ([[reconstruction]]) if they aren't already vectorized in NumPy.

## Rules of thumb we follow

- **Never do blocking work on the event loop** — writes and compression are offloaded.
- **Prefer NumPy/`zstandard`** (GIL-releasing C) over hand-rolled Python loops for any
  bulk byte work — this is also why the codec uses `numpy.tobytes` ([[bin-format]]).
- **Threads for I/O + GIL-releasing C** (writer, compression); **processes only** for
  pure-Python CPU (not needed on the capture path).
- **Isolate per stream** so one file's stall can't block others.

## Note on "no-GIL" Python

Python 3.13+ ships an experimental **free-threaded** build (GIL optional). We do **not**
depend on it — the design above already gives real parallelism where it matters (I/O +
zstd) on standard CPython. If a free-threaded runtime matures, our thread-based writers
and compression pool benefit automatically with no code change.
