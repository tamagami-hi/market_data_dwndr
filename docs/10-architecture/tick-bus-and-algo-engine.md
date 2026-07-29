---
title: Tick Bus and Algo Engine Pipeline
area: architecture
type: plan
status: proposed
tags: [area/architecture, type/plan, status/proposed, ipc, rust, greeks]
up: "[[Architecture-MOC]]"
related: ["[[rust-ipc-zmq-plan]]", "[[concurrency-and-gil]]", "[[tech-stack-and-efficiency]]"]
---

# Tick bus and algo engine pipeline

Refines [[rust-ipc-zmq-plan]] with the exact fan-out shape, the callback seam to hook, and
the measurements that motivated it. That note establishes *why* ZeroMQ over a Unix socket;
this one settles *where the data splits*, *what travels*, and *what must never be allowed
to block*.

Diagram: `tick-bus-pipeline.svg` (same folder).

Status: agreed in discussion 2026-07-29, not yet implemented.

---

## The pipeline

```
KiteTicker._on_message(payload, is_binary)          ← broker thread
   │
   ├─► on_message  → ZMQ PUB (raw bytes, DONTWAIT) ─────► Rust algo engine
   │                 topic: ticks                         [may drop, by design]
   │                                                      py_vollib-equivalent Greeks
   │                                                      strategy → order placement
   │
   └─► on_ticks    → parsed list[dict]
                     → call_soon_threadsafe → asyncio queue
                     → apply_ticks (in-place ints)
                     → 1 Hz grid snapshot
                          ├─► writer queue → .bin on disk   [in-process, never drops]
                          └─► py_vollib Greeks/IV → WS → TickVault frontend
```

**Fan-out at the source, not a chain.** The archive is *not* a bus subscriber. ZMQ `PUB`
discards at the high-water mark — correct for a trading engine, where a stale tick is
worthless, and catastrophic for the archive, whose entire purpose is completeness. Putting
the writer behind the bus would place the one thing that must not be lost behind a
transport engineered to drop.

The consequence to protect: the disk path stays byte-identical to what produces
`session_loss_pct ≈ 0.24%` today. The bus is purely additive. If a future change ever
makes the archive depend on the bus, that property is gone.

---

## Why `on_message` and not `on_ticks`

`KiteTicker._on_message` hands over the **raw payload before parsing it**:

```python
def _on_message(self, ws, payload, is_binary):
    if self.on_message:
        self.on_message(self, payload, is_binary)          # raw bytes, unparsed
    if self.on_ticks and is_binary and len(payload) > 4:
        self.on_ticks(self, self._parse_binary(payload))   # the existing hook
```

So `on_message` is strictly earlier in the callback order, and publishing there means:

- **zero serialisation on the algo path** — one `send(bytes)`, no parse, no re-encode
- **earliest possible delivery** — the engine does not wait behind Python's parse
- **no schema to invent** — Rust parses Kite's binary format natively

`on_ticks` receives `list[dict]`, already parsed; publishing from there would re-serialise
work Python had just done.

`app/kite/ticker.py` keeps its current role unchanged (thread → `asyncio.Queue` bridge via
`call_soon_threadsafe`). `engine.py`, `writer_thread.py` and the grid loop need no change.

---

## Four details that will bite otherwise

**1. ZMQ sockets are not thread-safe.** `on_message` fires on the KiteTicker background
thread (`ticker.py` docstring: *"KiteTicker runs its own background thread and invokes
on_ticks from there"*). Create the PUB socket **on that thread** and touch it from nowhere
else. A `zmq.Context` may be shared; a socket may not.

**2. The send must never block.** `send(payload, zmq.DONTWAIT)` wrapped for `zmq.Again`,
with a **dropped-message counter** surfaced in telemetry. A blocking send would stall the
thread feeding capture — the one path that must not stall. Without the counter, drops are
invisible.

**3. The engine cannot interpret ticks without the instrument map.** Kite frames are keyed
by `instrument_token`; meaning lives in chain assembly (`app/chain/filter.py`,
`app/chain/assembler.py`, both ported from `algo_engine`'s `filter.rs` / `assembler.rs`).
Publish a **session header** — token → (underlying, expiry, strike, side) — on its own
topic at capture start, and **republish periodically** so a restarted or late-joining
engine can resync. Otherwise the engine must start before capture and never miss the first
message, which is a fragile coupling.

**4. Sequence numbers and timestamps.** Every message carries a monotonic sequence and an
`ns` publish timestamp. `PUB` drops silently; a sequence gap is the only way the engine
learns it happened, and the timestamp is the only way to measure true end-to-end latency
rather than inferring it.

---

## Two parsers for one format

Rust will parse Kite's binary frames, and pykiteconnect's `_parse_binary` already does.
Two implementations of a format the vendor has changed before.

Mitigation: capture a handful of raw payloads to disk during a session as fixtures, then a
conformance test feeds each through both parsers and asserts identical output. Cheap to add
now, expensive to discover through a mispriced trade.

---

## Where the per-second time actually goes

Measured 2026-07-29 — real captured frames for Greeks, production-shape payloads for
encode:

| Stage | Cost |
|---|---|
| Greeks + IV, all four chains (real frames) | **16.0 ms** |
| chain metrics | 0.3 ms |
| option grids `json.dumps` + deflate | 7.3 ms |
| stock board columnar build | 2.7 ms |
| stock board `json.dumps` (310 KiB) | 5.1 ms |
| stock board deflate (→ 119 KiB wire) | **15.8 ms — per client connection** |
| **one browser client** | **47.2 ms** |
| **two browser clients** | **68.4 ms** |

Two findings that change the priorities:

**Serialisation and compression cost more than the Greeks.** The observed 50–90 ms is
real, but it is dominated by encode + deflate, not by the math.

**`permessage-deflate` runs per WebSocket connection.** Each additional browser tab adds
~21 ms of compression work. One tab ≈ 47 ms, two ≈ 68 ms, three ≈ 90 ms — which is exactly
the observed range, and why it appears to vary for no reason.

**`pipeline_ms` under-reports.** The metric covers the build only; `json.dumps` and deflate
happen later in the WS layer, outside the measured window. Worth either extending the
window or renaming the field.

---

## py_vollib: the win is accuracy, not speed

Current solver (`app/reconstruct/bs.py`): hand-rolled Newton seeded at `σ=0.2`, bisection
fallback over `[1e-4, 8.0]`, VIX-derived IV when both fail. Measured solve coverage on real
frames:

```
BANKNIFTY 176/202    FINNIFTY 166/202    NIFTY 175/194    SENSEX 174/202
```

**13–18% of strikes produce no solved IV** and fall back to a VIX-derived figure — a
fabrication, not a measurement, and every Greek derived from it inherits that.

py_vollib is built on Peter Jäckel's *Let's Be Rational*: an analytic rational
approximation, machine precision in a fixed ~2 iterations, no seed sensitivity, no bracket
failure. Expected to take the unsolved fraction to near zero.

**It also improves Rust parity.** With py_vollib in Python and the `implied-vol` crate (a
direct LBR port) in Rust, the two implementations agree to near machine precision. The
current Newton-plus-VIX-fallback would *not* have matched a Rust LBR implementation — so
adopting py_vollib makes the dual-implementation architecture safer, not riskier.

Open items before the swap:

- **Model convention.** `bs.py` is spot Black-Scholes, no dividend yield, theta per
  calendar day, vega/rho per 1% — mirroring `algo_engine`'s `oc_maker/bs_models.rs`.
  py_vollib offers both `black_scholes` (spot) and `black` (Black-76, forward). Pick one
  and enforce it on both sides.
- **Parity break.** Conventions such as the `365.25`-day year, `MIN_MATURITY_YEARS = 1e-5`
  and the intrinsic tolerance were chosen for `algo_engine` parity. Swapping solvers shifts
  values slightly; version the convention if stored reconstructions are ever compared
  across releases.
- **The VIX fallback.** If Rust does not replicate it, the two diverge precisely on the
  strikes where Python was guessing. Preferably the fallback becomes rare — verify, do not
  assume.

Greeks are **never persisted** (`.bin` holds raw ints; Greeks are recomputed on read), so
moving or changing the computation cannot invalidate history.

---

## Python parallelism: the wrong lever

- **Threads** cannot help: `bs.py` is pure-Python `math`, fully GIL-serialised. The code
  already notes it (*"this runs ~1k times/second on the display thread… it holds the GIL"*).
- **Processes** cost more than they save: pickling plus pool overhead to distribute ~16 ms
  across four chains.
- **Free-threaded CPython** makes threads real but the numeric ecosystem is uneven — not a
  bet to place under production capture.

The lever that pays is **vectorisation**: `py_vollib_vectorized` replaces ~202 per-strike
Python solves per chain with a few numpy array ops, removing interpreter overhead rather
than adding cores. At 101 strikes the arrays are small, so expect the win from eliminating
per-strike overhead, not from SIMD width.

In Rust the question disappears: ~808 IV solves with LBR is tens of microseconds. Rayon is
available and almost certainly unnecessary.

---

## Reducing the 50–90 ms, by payoff

1. **Deflate is per-connection and the largest line item.** Drop compression on the stocks
   topic (119 KiB vs 310 KiB wire — LAN/Tailscale has the headroom), lower the zlib level,
   or move to a binary frame.
2. **The stock board does not need 1 Hz for a human.** Nobody reads 210 stocks × L1–L5 every
   second. Throttle when no client has the page open; the archive is written independently.
3. **Send binary instead of JSON.** The `.bin` layout already exists and a browser can decode
   it with a `DataView` — removes the encode and most of the compression, since packed ints
   compress far better than JSON floats.
4. **py_vollib** for the 16 ms.

Note that py_vollib alone leaves ~31–74 ms untouched, including all of the part that scales
with viewers.

---

## Transport notes

- **`ipc://` (Unix socket)**, not `tcp://`, while both processes share a host. Same API, no
  TCP stack. Move to `tcp://` only if the engine relocates.
- **Across containers**, the socket file needs a shared mount (see [[rust-ipc-zmq-plan]]).
- **Shared-memory ring buffer** (`rtrb`, iceoryx) is faster still, at the cost of losing the
  option to move the engine off-host. ZMQ is the pragmatic default.
- **`PUB`/`SUB` only** on the capture side. Never `PUSH` or `REQ`, which can block the
  publisher.

---

## Sequencing

Each step independently useful, so none depends on the next landing:

1. **Verify the live figure** with a known number of open tabs, and confirm the per-client
   deflate multiplier during market hours.
2. **Swap in py_vollib (vectorized)** — contained, fixes the unsolved-IV problem immediately,
   improves the dashboard and any historical analysis.
3. **Build the bus**: `on_message` → PUB raw bytes, plus the session-header topic. Rust
   subscribes and computes its own Greeks. Keep Python's Greeks for display; accept the
   temporary duplication rather than coupling two rollouts.
4. **Retire nothing** until Rust output has been diffed against Python's over a full session.

Explicitly rejected: Python multiprocessing for Greeks — most complexity, least benefit.
