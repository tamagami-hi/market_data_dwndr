---
title: Distributed Capture Architecture Plan
area: architecture
type: plan
status: proposed
tags: [area/architecture, type/plan, status/proposed, zmq, ipc, capture, writer, sessions]
up: "[[Architecture-MOC]]"
---

# Distributed Capture Architecture Plan

This plan documents the as-built capture infrastructure and proposes splitting it into
independent services. The shape is a sequential ZMQ stage between parallel halves: fetchers
(WebSocket primary, REST warm standby) push into `md-tickbus`, which arbitrates and fans out
to two parallel consumers — a session-aware adaptive BIN writer and a combined
Greeks-plus-browser streaming gateway. It is a design pass only; no implementation is
included.

Three things drive it. First, a monolithic process makes every recovery a whole-pipeline
recovery: a dead WebSocket restarts the writer, the market state, and the browser feed along
with it. Second, the display is pinned to the archive's 1 Hz grid only because it shares a
snapshot with it — decoupling them lets the display be more realtime than the archive. Third,
the market's closing structure changed on 2026-08-03 and the single 09:00–15:30 window in this
repo no longer matches any segment's real hours.

One authentication boundary is fixed for every phase of this design: **Calspread is the
session authority**. An administrator logs in to Kite through `calspread.online`; Calspread
uses the Kite API key and API secret to complete that login and retains the access token Kite
returns. This downloader never creates, exchanges, refreshes, or rotates a Kite access token.
It only retrieves the already-issued token from Calspread, validates it, persists a local
restart cache, and uses `api_key + access_token` for market-data REST and WebSocket calls.
Repeated retrievals return the same token until an administrator logs in again through
Calspread. Feed recovery must therefore restart or replace the failed transport while reusing
the same token; it must never treat token retrieval as a reconnect strategy.

---

## 1. Problem statement

Three symptoms were reported: the WS feed sometimes stops delivering, sometimes delivers
stale data, and recovery costs many seconds. All three are already root-caused in
`logs/progress-log.md` (2026-08-07) and partially mitigated. What remains unsolved is the
*cost* of recovery, because recovery is currently "exit the process and let Docker restart
it".

Current recovery cost, from the code and the tests that pin it:

| Component | Value | Source |
|---|---|---|
| Staleness tolerance before writes are suppressed | 5 s | `CAPTURE_STALE_SECONDS`, `config.py:164` |
| Continuous staleness before escalation | 60 s | `CAPTURE_STALE_EXIT_SECONDS`, `config.py:185` |
| Recovery-arm grace after open | 300 s | `CAPTURE_RECOVERY_ARM_DELAY_SECONDS`, `config.py:220` |
| Measured restart downtime | 25–30 s | `test_restart_resume.py:120` asserts `25 <= downtime_seconds <= 30` |
| Worst case per escalation | ~85–90 s of missing seconds | 60 s detection + 25–30 s restart |
| Restart budget per trading date | 3 (warn only; restarts continue) | `CAPTURE_STALE_EXIT_MAX_RESTARTS`, `config.py:197` |
| Observed worst incident | 5,445 stale seconds (23.4% of 2026-08-06) | `logs/progress-log.md` |

The 25–30 s restart is not the writer's fault — the engine's `finally` drains and fsyncs
before exiting, so no *captured* frame is lost. The cost is bootstrap: instrument dump
load, chain assembly, board discovery, `scan_frames` over ~4.5 GB of files, and
re-subscription. Restarting a WebSocket should not require any of that.

There is no REST fallback for ticks today. `app/kite/quotes.py` is bootstrap-only ATM
seeding (`fetch_ltp`, called once from `bootstrap.py:104`). During a stale spell the
pipeline deliberately writes nothing and leaves an auditable hole. That is the correct
choice given the current design, but a hole is still missing data when a REST snapshot
could have filled it.

---

## 2. Current infrastructure (as-built)

### 2.1 Process topology

Everything runs in **one uvicorn process, one worker**, because capture state lives in
that process's memory.

```
container: market-data-dwndr-backend      (compose.yaml:18)
  CMD python -m app.server                (backend/Dockerfile:27)
    └── uvicorn app.main:app              (app/server.py:27)  no --workers
         │
         ├── lifespan startup             (app/main.py:83)
         │     ├── SessionService                (retrieved token + risk-free rate, _state/)
         │     ├── CaptureController              (owns the capture task)
         │     ├── DailyAutomationService         (5 s asyncio tick, not cron)
         │     └── ConnectionManager ws_hub       (shared BY REFERENCE with capture)
         │
         ├── thread: KiteTicker (SDK-owned, threaded=True)
         │     on_ticks → loop.call_soon_threadsafe → asyncio.Queue(10_000, drop-oldest)
         │
         ├── event loop
         │     CaptureEngine._consume   → apply_ticks()      [sync, NO await]
         │     CaptureEngine.run()  1 Hz → capture_snapshot() [sync, NO await]
         │                              → writer.enqueue()
         │                              → broadcaster.publish_latest()  fire-and-forget
         │                              → observe_feed_health()
         │
         ├── threads: FileWriterThread × N (one per .bin file, UNBOUNDED queue)
         │     open → write_header (once, if st_size==0) → append+flush+fsync per frame
         │
         └── asyncio.to_thread workers
               _build_snapshot_messages  (Greeks + IV + JSON build)
               sync_external_session      (currently named acquire_broker_session)
               compress_raw_files         (zstd L17 over ~8 GB, EOD, in-process)

container: market-data-dwndr-frontend     (compose.yaml:65)
  depends_on: backend service_healthy
```

`app/capture/run.py` (`md-capture`) is a second, headless entrypoint that already exists
and is not used by any compose file. It has no WS hub, and it duplicates the market-hours
watcher and EOD trigger that `DailyAutomationService` owns.

### 2.2 Module inventory relevant to the split

| Area | Files | Role |
|---|---|---|
| Uplink | `kite/ticker.py`, `kite/ticks.py`, `capture/subscription.py` | `TickerBridge`, full-mode subscribe, paise conversion, token budgeting |
| State + grid | `capture/engine.py`, `chain/table.py`, `stocks/matrix.py`, `index_fno/matrix.py` | tick routing, 1 Hz grid, frame build, sequence |
| Health | `capture/feed_health.py`, `capture/reconnect.py`, `capture/monitor.py` | 3-signal classify, `FreshnessMonitor`, telemetry |
| Persistence | `bin_codec/{layout,writer,compress,reader,scan}.py`, `capture/writer_thread.py` | format, file handles, fsync, EOD zstd, resume scan |
| Sessions | `ops/sessions.py`, `ops/calendar.py`, `ops/automation.py`, `config.py` | `MarketSession`/`SessionRegistry`, phases, daily policy |
| Analytics | `reconstruct/{bs,greeks,metrics,spreads}.py`, `chain/*` | IV solve, Greeks, ATM/max-pain/PCR |
| Streaming | `capture/broadcaster.py`, `ws/{protocol,routes}.py` | message build, coalescing publish, topic hub |
| Control | `main.py`, `api/*`, `session.py`, `session_service.py`, `ops/{eod,retention,completeness,stats_store}.py` | auth, automation, EOD, status/health/stats |

### 2.3 Measured capacity

Reliability work is not CPU-constrained. From `docs/30-live-capture/live-capture-performance.md`
and `docs/10-architecture/tick-bus-and-algo-engine.md`:

| Stage | Cost per second | Share of 1000 ms budget |
|---|---|---|
| `apply_ticks` (production flow) | 3.59 ms | 0.36% |
| `capture_snapshot` (all domains) | 0.20 ms | 0.02% |
| Greeks + IV, four chains | 16.0 ms | 1.6% |
| Chain metrics | 0.3 ms | 0.03% |
| JSON encode (grids + stock board) | 12.4 ms | 1.2% |
| **permessage-deflate, per browser connection** | **~15.8–21 ms** | scales with viewers |
| One browser client, end to end | 47.2 ms | 4.7% |
| Two browser clients | 68.4 ms | 6.8% |

Storage: ~8.9 GB/day raw → ~1.5–2.5 GB/day compressed. Live table memory 1.2 MB.
Subscription: **2,067 tokens measured** on **one** WS connection, against a 2,700 safe
threshold and Kite's 3,000 hard cap. `SubscriptionPlan.shards()` exists and has no caller.

Derived breakdown (verify with `plan_subscriptions(...).as_telemetry()` before relying on it):

| Domain | Tokens |
|---|---|
| 6 index chains (101 strikes × 2 sides) | ~1,212 |
| 6 index spots + India VIX (264969) | ~7 |
| Stock matrix (~210 stocks × spot + 3 futures) | ~840 |
| Index F&O (6 underlyings × spot + 3 futures, spots deduped) | ~18 |
| **Total** | **~2,067** |

### 2.4 Load-bearing invariants

These are enforced by tests and stated as locked in `docs/30-live-capture/live-data-pipeline.md`.
Any split must preserve them.

1. **Snapshot consistency without a lock.** `apply_ticks` and `capture_snapshot` are both
   synchronous, non-yielding functions on the same event loop, so a frame can never hold a
   half-applied batch. Inserting any `await` between building the index frames and the stock
   frame produces datasets that disagree about the instant they represent.
2. **A stale second is never written.** The `.bin` layout is fixed-width, so a frame built
   from duplicated last-known values is byte-indistinguishable from a real one.
   `test_stale_write_suppression_e2e.py:124` pins the exact hole.
3. **`sequence` advances once per grid second *built*.** A skipped second leaves a gap in
   the on-disk sequence, which is the audit trail
   (`test_stale_write_suppression_e2e.py:235`).
4. **Suppression is per grid second, not per stream.** The index file and the stock file must
   carry identical timestamp tuples (`assert list(reader.timestamps) == timestamps`).
5. **Header written only when the file is empty** (`st_size > 0` check, `writer.py:162`), so
   a mid-day restart appends.
6. **Loss denominators come from the schedule, not from uptime.** `ops/completeness.py`
   reconciles the scheduled grid against frame timestamps on disk and consults no telemetry,
   because a crash destroys telemetry exactly when it matters.
7. **The frontend never applies backpressure to ingest or disk.** Stated in
   `docs/60-operations/operations-runbook.md`.
8. **Greeks are never persisted.** Recomputed on read from raw ints plus the header's
   risk-free rate.

---

## 3. Findings

### F1 — The capture window loses 10 minutes of derivatives data every trading day

Effective **2026-08-03**, NSE restructured the close
([NSE extends F&O to 3:40 pm](https://economictimes.indiatimes.com/markets/stocks/news/nse-extends-equity-fo-segment-timings-till-340-pm-from-august/articleshow/131404896.cms),
[Zerodha CAS bulletin](https://zerodha.com/marketintel/bulletin/439989/introduction-of-closing-auction-session-cas-in-the-equity-cash-segment-and-certain-modifications-in-the-pre-open-auction-session)).
Content rephrased for compliance with licensing restrictions.

| Segment | Continuous trading | After |
|---|---|---|
| Cash, F&O-eligible stocks (**this repo's entire spot universe**) | 09:15 – **15:15** | CAS 15:15–15:35: reference calc 15:15, order entry 15:20, random close 15:28–15:30, matching 15:30–15:35 |
| Cash, non-F&O stocks (not captured) | 09:15 – **15:30** | — |
| Index spot values | 09:15 – **15:15** continuous | Indicative 15:15–15:30 (NSE: no continuous order matching in this window); final index level from CAS prices ~15:35 |
| **Index and stock derivatives** | 09:15 – **15:40** | — |
| Cash post-close (trades at closing price) | — | 15:50–16:00 |

The deployed `backend/.env:44` sets `MARKET_CLOSE=15:30` and defines no `EQUITY_*` block, so
both `MarketSession`s fall back to an identical 09:00–15:30 window. Consequences, all live today:

- **15:30–15:40 derivatives ticks are not captured at all.** `is_capture_expected` is false, so
  `capture_snapshot` counts `unscheduled_seconds` and writes nothing. 600 s/day × 6 index chains
  + stock futures + index futures, gone. This includes the final ten minutes of every expiry.
- **15:30–15:35 CAS price discovery is not captured**, which is now the mechanism that sets the
  official close for every F&O stock and, through them, every index.
- **15:15–15:30 the stock spot leg and index spots are legitimately frozen** while derivatives
  keep ticking. `_is_whole_feed_stale` correctly does not escalate, but `stale_artifacts` fires
  daily at the 15 s artifact threshold and `_report_stale_artifacts` logs it as a fault-shaped
  event. Real staleness and structural staleness are indistinguishable in the current model.
- **09:00–09:15 is counted as scheduled** (`MARKET_OPEN=09:00`), inflating the loss denominator
  by 900 s/day and re-creating the exact pre-open condition that started the 2026-08-04/05/06
  incidents. `CAPTURE_RECOVERY_ARM_DELAY_SECONDS=300` masks it rather than fixing it.
- **A further pre-open change lands 2026-09-07** (three weeks out): matching moves to 09:10–09:12
  with transition 09:12–09:15. `.env.example:79-80` proposes `CAPTURE_START_TIME=09:10`, which would
  start capture inside the new matching phase.

Note `.env.example` already proposes `CAPTURE_END_TIME=15:42`, but the deployed `.env` does not
set it, and `_validate_daily_schedule` (`config.py:502`) validates the auth/capture window rather
than the session window, so the mismatch is not caught.

### F2 — Recovery granularity is the whole process

`CaptureStalledError` → `_default_fatal_handler` → `SIGTERM` → Docker `restart: unless-stopped`
(`api/capture.py:58`). The blast radius of a dead socket is: market state, all writers, the
browser feed, the automation scheduler, and 25–30 s of bootstrap. The escalation ledger
(`escalations-<date>.json`) exists specifically to stop this from looping all day.

### F3 — Sharding and reconnect are coded but unwired

`SubscriptionPlan.shards()`, `ReconnectPolicy` (5 s → 300 s, 20 attempts),
`TickerBridge.reconnect()`, and `set_token_provider` have no live callers. All 2,067 tokens sit
on one socket in `full` mode, so one dead socket is a total outage. `plan_subscriptions` logs
`error` past 2,700 and `critical` past 3 connections, then subscribes everything anyway; a
`subscribe` failure inside `_on_connect` is caught and logged, leaving a connected socket
carrying no data (`ticker.py:104`).

`docs/30-live-capture/live-data-pipeline.md` leaves one question deliberately open: whether an
in-process `reconnect()` can re-establish a socket at all under kiteconnect 5.x, whose
`connect(threaded=True)` runs a process-global twisted reactor. **The drill has never been run.**
A separate uplink process sidesteps the question entirely — a fresh process gets a fresh reactor.

### F4 — No REST fallback exists, and a naive one cannot keep up

Verified against [Kite Connect docs](https://kite.trade/docs/connect/v3/market-quotes/) and
[exceptions/rate limits](https://kite.trade/docs/connect/v3/exceptions/):

| Endpoint | Instruments per call | Rate limit | Fields |
|---|---|---|---|
| `/quote` | **500** | **1 req/s** | full: OHLC, OI, `oi_day_high/low`, volume, buy/sell qty, 5-level depth, `timestamp`, `last_trade_time` |
| `/quote/ohlc` | 1000 | 1 req/s | OHLC + LTP only |
| `/quote/ltp` | 1000 | 1 req/s | LTP only |

The aggregate budget is **500 full-fidelity instruments per second**. Covering 2,067 tokens takes
`ceil(2067/500) = 5` calls, i.e. a **5-second full sweep**. REST cannot replicate a 1 Hz feed for
the whole universe. Any fallback must be tiered and honest about its fidelity.

Field parity is achievable: `/quote` is a superset of WS `full` mode for every column the BIN
layout stores. Two mechanical differences: `/quote` is keyed by `exchange:tradingsymbol` (a
reverse token → symbol map is needed; the instrument dumps have it), and it returns
`timestamp`/`last_trade_time` as exchange-side clocks — which is exactly what open question C in
`docs/90-decisions/decisions-and-open-questions.md` asked for and the archive currently lacks.

### F5 — Streaming cost is on the capture path and scales with viewers

Greeks are 16 ms; JSON encode plus per-connection deflate is 27–48 ms and grows ~21 ms per open
browser tab. `_build_snapshot_messages` runs in `asyncio.to_thread`, which keeps the loop
responsive but buys no parallelism for pure-Python `math` under the GIL. The stock board ships all
5 depth levels for all 4 legs of ~210 stocks every second (310 KiB JSON → 119 KiB deflated),
contradicting `docs/50-frontend/websocket-protocol.md`, which claims depth is excluded.
`OptionGridDelta` and `KEYFRAME_INTERVAL = 30` are implemented and dead.

### F6 — IV solve fails on 13–18% of strikes

`implied_vol` is a hand-rolled Newton solve seeded at σ=0.2 with a bisection fallback. Measured
coverage: 176/202 BANKNIFTY, 166/202 FINNIFTY, 175/194 NIFTY, 174/202 SENSEX. The remainder fall
back to a VIX-derived IV, and every Greek on those strikes inherits a fabricated number.

### F7 — Writer has no backpressure and no file segmenting

`FileWriterThread._queue = queue.Queue()` with no maxsize (`writer_thread.py:53`). Safe at 1 Hz
in-process; across a socket it needs an explicit bounded protocol. Durability is
write + flush + fsync **per frame** with no group commit. There is no rotation, no size cap, and
the header's `N` (strike/stock/index count) is cached at `write_header` — so the strike vector or
board composition cannot change mid-day without a new file.

### F8 — Documentation drift that will mislead implementation

- `docs/60-operations/failure-modes.md` still documents the deleted 5 s→300 s backoff ladder and
  the rule "prefer pausing/retrying over crashing", which restart-first contradicts.
- `docs/20-data-and-storage/storage-layout.md` omits `INDICES_FnO/` and `_state/`, and lists a
  `_meta/` directory with no code reference.
- `docs/50-frontend/websocket-protocol.md` and `frontend.md` claim Greeks are not in the feed and
  that `StockBoard` excludes depth. Both are false.
- `docs/60-operations/vps-docker-deployment.md` references `deploy/preflight.sh` and
  `deploy/market-data-dwndr.service.example`, neither of which exists.
- `compose.yaml` sets `stop_grace_period: 30s` while `api/capture.py:34` allows
  `STOP_TIMEOUT_S = 45.0` for writer flush. Docker will SIGKILL first.

### F9 — Authentication ownership is described incorrectly

Several documents and code names conflate retrieving Calspread's existing token with creating
or refreshing a Kite session. Calspread is the sole login/session authority; the downloader
cannot make Calspread mint a different token by polling it. Only an administrator relogin in
Calspread changes the published token.

The current `refresh_broker_session`, capture token-refresher callback, session invalidation,
and local login/TOTP/request-token paths embody the older model. They are implementation debt,
not capabilities to reproduce in the distributed services. In particular, a feed stall must
never call Calspread or mutate credential state, and an explicit Kite authentication rejection
must become a stable operator-action state instead of a same-token restart loop.

---

## 4. Target architecture

### 4.1 Topology: bus inline, consumers parallel

The bus is a service in the data path, immediately downstream of the fetchers. The writer
and the stream gateway are parallel siblings off it — neither feeds the other, and neither
can delay the other.

```
   AUTHORITY PLANE (not a market-data recovery path)

   Admin ── broker login ──▶ calspread.online ── existing access token ──▶ md-control
                                  │                                      │
                                  │ only admin relogin rotates token      └─ atomic local
                                  │                                         session cache
                                  ▼
                              Kite session

   MARKET-DATA PLANE (all uplinks use the same api_key + access_token)

   Kite WS  ┌──────────────────────┐
  (shard A)─│  md-uplink-ws   A    │─┐
            └──────────────────────┘ │
   Kite WS  ┌──────────────────────┐ │  PUSH  NormTick batches
  (shard B)─│  md-uplink-ws   B    │─┤  ipc:///run/mdbus/ingest
            └──────────────────────┘ │
   Kite REST┌──────────────────────┐ │
  /quote 1/s│  md-uplink-rest      │─┘  polls CONTINUOUSLY, warm standby
            └──────────────────────┘
                                     │
                      ═══════════════ ▼ ═══════════════   SEQUENTIAL STAGE
                        ┌───────────────────────────┐
                        │        md-tickbus         │  one job, no market state:
                        │  per-token arbitration    │   • pick WS or REST per token
                        │  dedupe                   │   • drop the losing source
                        │  stamp global seq + ns    │   • stamp canonical ordering
                        │  fan out                  │   • forward raw WS verbatim
                        └───────┬───────────┬───────┘
                      ══════════│═══════════│══════════   PARALLEL CONSUMERS
                   PUB ticks    │           │    PUB ticks
              ipc://…/ticks.w   │           │  ipc://…/ticks.s   (+ raw for Rust)
                                ▼           ▼
              ┌──────────────────────┐   ┌──────────────────────┐
              │      md-writer       │   │      md-stream       │
              │ own tables + grid    │   │ own tables + grid    │
              │ per-segment phases   │   │ greeks / IV / metrics│
              │ build → fsync        │   │ encode → browser WS  │
              │ 1 Hz (archive grid)  │   │ 1–4 Hz, per topic    │
              └──────────┬───────────┘   └──────────▲───────────┘
                         │  PUB write receipts      │
                         └──────────────────────────┘
                            ipc://…/receipts  (tiny, ~40 B/s)
                         │
              MARKET_DATA (SSD) ── EOD ──▶ ARCHIVE (HDD)
                         │
              ┌──────────────────────┐
              │      md-control      │  SUB health from all, REQ/REP commands,
              │ session sync + EOD   │  sole credential/cache owner
              └──────────────────────┘
```

Six backend service roles: `md-uplink-ws`, `md-uplink-rest`, `md-tickbus`, `md-writer`,
`md-stream`, and `md-control`. Because the WS role has two-to-three shard instances, this is
seven-to-eight backend processes/containers, plus the existing frontend container.

### 4.2 What changed from the first draft, and why this shape is better

The first draft put a full aggregator between the fetchers and the bus: it owned market
state, built frames, and shipped **finished frames** to the writer over an acked channel with
a write-ahead log. Moving the bus upstream of the consumers removes that entirely, and the
consequences are mostly good.

| | First draft (aggregator builds frames) | This draft (consumers build frames) |
|---|---|---|
| What crosses IPC | finished `.bin` frames | normalized ticks |
| Frame durability across IPC | needs per-frame ACK + producer WAL | **not needed — no frame crosses a socket** |
| Loss of one message | a frame is gone; must be replayed | a tick is superseded by the next one at 1 Hz |
| Display and archive cadence | locked together | **independent** |
| Market state | one copy | two copies |
| Display/archive agreement | guaranteed by construction | needs a receipt topic to verify |
| Invariant 1 enforced in | one process | two processes |

The durability point is the significant one. My first draft created a problem — "how do I
guarantee a frame survives a socket hop?" — that only existed because I put frame
construction upstream of the socket. Building frames inside the writer means the sequence
`apply ticks → snapshot → encode → write → fsync` never leaves one process address space.
The ACK protocol and the WAL both disappear. That is a real simplification, not a
rearrangement.

It also makes putting the archive downstream of the bus acceptable, which
`docs/10-architecture/tick-bus-and-algo-engine.md` warned against. That warning was about
frames: "putting the writer behind the bus would place the one thing that must not be lost
behind a transport engineered to drop." Ticks are different. A dropped tick at 1 Hz
last-value-wins is superseded by the next update — which is exactly the trade the existing
`asyncio.Queue(maxsize=10_000)` drop-oldest policy already makes on this path
(`ticker.py:133`), and `test_ticker.py:82` pins it as intended behaviour. The bus hop does
not change the risk class of the persistence path; it changes where the queue lives.

### 4.3 Honest accounting of the latency claim

The bus does not make the pipeline faster. It adds a hop.

| Path | Today | This design |
|---|---|---|
| tick → writer decision | in-process call | + ~2 × 30–50 µs (`ipc://` round of PUSH then PUB) ≈ **+0.1 ms** |
| tick → browser | in-process, but shares the event loop with `apply_ticks` | + ~0.1 ms, on a dedicated loop |

What the topology actually buys is three things, and they are worth more than 0.1 ms:

1. **The slow consumer stops competing with the fast one.** Today `publish_latest` is
   fire-and-forget, but the work it schedules is not free: `_build_snapshot_messages` runs in
   `asyncio.to_thread` (GIL-bound pure-Python `math`), and `hub.broadcast` awaits on the same
   event loop that runs `apply_ticks`. Measured, that is 47 ms for one browser tab and 68 ms
   for two, growing ~21 ms per additional tab because `permessage-deflate` runs per
   connection. All of it currently shares a loop with tick application.
2. **Independent cadences.** This is the biggest realtime win and it is only available in this
   topology. The writer is pinned to the 1 Hz archive grid because the `.bin` contract says
   so. The display has no such constraint — it is pinned to 1 Hz today only because it
   piggybacks on `capture_snapshot`. Decoupled, the option chain can push at 2–4 Hz while the
   archive stays at 1 Hz. **The display can become more realtime than the archive.**
3. **Failure isolation.** A dead browser client, a wedged deflate, or a Greeks exception
   cannot touch the write path, and vice versa.

### 4.4 Where Greeks live

Folded into `md-stream`, as one service — matching how you described it. Greeks have exactly
one consumer (the display), are never persisted, and cost 16 ms against `md-stream`'s own
budget. A separate `md-analytics` would need to rejoin its output to the streamer's frames on
`grid_ts`, which is extra machinery for no current benefit. The future Rust engine computes
its own Greeks per
`docs/10-architecture/tick-bus-and-algo-engine.md`, so there is no external consumer.

Split it out later only if `md-stream`'s per-second budget becomes the constraint. Cheaper
first move: `py_vollib_vectorized`, which addresses F6 and cuts 16 ms to roughly 2 ms.

### 4.5 Session authority and credential distribution

Authentication is a control-plane dependency, not part of the tick recovery loop.
`calspread.online` is the only component that performs the interactive Kite login and the
`request_token` → `access_token` exchange using the API secret. `market_data_dwndr` receives
only the resulting access token through the protected Calspread endpoint.

`md-control` is the sole Calspread client and the sole writer of local session state:

1. Retrieve the currently established Calspread token during the configured morning window
   or when no usable local session exists.
2. Validate it directly with Kite using the configured API key and the target's non-secret
   `KITE_EXPECTED_USER_ID` account binding. The API key must be the same Kite application key
   under which Calspread obtained the token.
3. Atomically persist the credential revision in `md-control`'s private durable credential
   volume. Persist trading-date/risk-free-rate metadata separately under `_state/`.
4. Keep the token fingerprint internal to `md-control`. Expose only credential readiness,
   opaque revision, and timestamps to health/control messages. Never place the token in ZMQ
   health, logs, metrics, or application control-plane/browser-visible URLs; redact the
   SDK-required Kite WebSocket handshake URL.
5. Atomically project the active credential into a narrowly mounted runtime credential volume
   at startup or after a different Calspread token has validated and advanced the durable
   credential revision. Every WS shard and the REST poller
   use the same validated token; writer, tickbus, stream, and browser services cannot mount or
   receive it.

Retrieval is idempotent. Calling Calspread ten times yields the same token ten times unless
an administrator has completed a new broker login in Calspread. Consequently:

- a WebSocket stall, disconnect, Twisted reactor failure, or REST error restarts only the
  affected uplink with the existing token;
- a backend or container restart first reuses the protected local session cache and may
  reconcile it with Calspread, but does not expect reconciliation to rotate credentials;
- only an explicit Kite authentication rejection moves the system to `AUTH_BLOCKED`;
- while `AUTH_BLOCKED`, `md-control` may poll Calspread for an administrator-created change,
  but the same rejected token cannot recover capture and must not be repeatedly installed;
- on entry to `AUTH_BLOCKED`, preserve the durable rejected record but atomically remove the
  usable runtime token projection or replace it with a tokenless blocked marker, so restarted
  uplinks fail closed;
- capture resumes only after Calspread returns a different token and Kite validates it;
- the downloader contains no normal or fallback path that accepts broker passwords, TOTP, a
  Kite API secret, or a `request_token`.

The target deployment therefore removes `KITE_API_SECRET`, `KITE_PASSWORD`, direct TOTP login,
and token-exchange responsibilities from this downloader. Those belong to Calspread. The
current repository still contains legacy direct-login and `refresh_broker_session` code; this
plan documents the target trust boundary only, and their removal is a separate implementation
task outside this documentation change.

---

## 5. `md-tickbus` — the sequential stage

### 5.1 One job

`md-tickbus` holds no market state, no tables, no frames, and touches no disk. It keeps one
small dictionary — `last_seen_ns[token]` per source — and does four things:

1. **Arbitrate per token.** Decide whether token `T`'s next value comes from WS or REST.
2. **Dedupe.** Drop the losing source's tick for that token, so downstream sees one value per
   token per instant and cannot make an inconsistent choice.
3. **Stamp.** Assign a global monotonic `seq` and `recv_ns`, establishing a canonical order
   that both consumers observe identically.
4. **Fan out.** PUB to the writer and the streamer on separate endpoints; separately PUB the
   raw unparsed WS payload for the future Rust engine.

Everything else stays out. The reason is failure surface: this process is the single inline
stage, so its recovery time is the pipeline's floor. Stateless-apart-from-timestamps means it
restarts in well under a second and rebuilds its arbitration state within one staleness
window.

### 5.2 Why arbitration belongs here and nowhere else

It cannot live in the uplinks: the REST poller would need to know WS health, and a WS shard
knows nothing about the other shard.

It could live in each consumer, but then the writer and streamer each run their own copy and
can disagree about which source is authoritative for token `T` at instant `t`. That is the
one divergence this design must not permit, because it would mean the display shows a value
that never enters the archive. Putting arbitration in the bus makes the choice single-sourced
by construction — the consumers never see the losing tick, so they cannot choose differently.

This is what earns `md-tickbus` its place as a service rather than a pointless hop. A bus that
only forwarded bytes would be exactly the broker process I argued against in the first draft;
a bus that owns arbitration is doing work no other component can do correctly.

### 5.3 Arbitration rule

Per token, with the existing hysteresis thresholds so the flicker fix from 2026-08-07 carries
over unchanged:

```
ws_age = now_ns - last_seen_ns[token][WS]

if ws_age < STALE_AFTER (5 s):
        emit WS tick;  drop REST tick for this token
else:
        emit REST tick, source=REST_FULL, flags |= DEGRADED
        return to WS only after 15 s of sustained WS freshness for this token
                (CAPTURE_STALE_RECOVERY_CONFIRM_SECONDS)
```

Per token, not per feed. Today `_is_whole_feed_stale` (`engine.py`) requires the transport
dead *or* every artifact stale, which is correct for deciding whether to restart and wrong
for deciding whether to fall back. One frozen index chain should switch that chain to REST
without touching anything else.

### 5.4 Wire format: `NormTick`

The uplinks normalize; the bus does not parse. This puts format knowledge where it belongs —
the WS shard knows Kite's binary layout, the REST poller knows the JSON — and guarantees the
two sources are indistinguishable downstream except by the `source` byte.

`NormTick`, 336 bytes fixed, little-endian, field order deliberately aligned to
`INSTR_SCALAR_COLUMNS` + `DEPTH_LEVEL_COLUMNS` in `bin_codec/layout.py` so a consumer can copy
straight into its matrix columns with no field-by-field translation:

| Offset | Field | Type |
|---|---|---|
| 0 | `instrument_token` | `u32` |
| 4 | `source` | `u8` — `WS_A`, `WS_B`, `WS_C`, `REST_FULL`, `REST_LTP` |
| 5 | `flags` | `u8` — `DEGRADED`, `PARTIAL`, `LTP_ONLY` |
| 6 | `_pad` | `u16` |
| 8 | `exchange_ts_ms` | `u64` |
| 16 | `last_trade_time_ms` | `u64` |
| 24 | `recv_ns` | `u64` |
| 32 | `ltp` | `i64` paise |
| 40 | `oi`, `volume`, `buy_qty`, `sell_qty`, `oi_day_high`, `oi_day_low` | `6 × u64` |
| 88 | `ohlc_open/high/low/close` | `4 × i64` paise |
| 120 | depth `[5]` of (`bid_price i64`, `bid_qty u64`, `bid_orders u32`, `ask_price i64`, `ask_qty u64`, `ask_orders u32`) | `5 × 40 B` |
| 320 | `_reserved` | `16 B` |

Index option chains use `RAW_BLOCK_COLUMNS` (L1 only, bid/ask inline) while stocks use
`INSTR_SCALAR_COLUMNS` + 5 levels, so one `NormTick` shape serves both: the index consumer
reads `depth[0]` and ignores the rest.

Messages are multipart `[topic][BusHeader][NormTick × N]` — batched to amortise syscalls, with
`N` from one uplink batch. `BusHeader` carries `magic`, `bus_version`, `msg_kind`, `seq`,
`publish_ns`, `count`. Peak volume: ~5,000 ticks/s × 336 B ≈ 1.7 MB/s, which `ipc://` handles
without noticing.

Persisting `exchange_ts_ms` closes open question C in
`docs/90-decisions/decisions-and-open-questions.md`. Without it the archive cannot tell "we
did not receive it" from "the exchange did not print it" — and once REST fallback exists, that
distinction is the difference between a bug and a quiet market.

No msgpack, no bincode, no Parquet, per the standing decision in
`docs/10-architecture/tech-stack-and-efficiency.md`. `RAW_TICK` forwards Kite's payload
verbatim, which also gives the raw-fixture corpus that plan's two-parsers conformance test
needs.

### 5.5 Endpoints and socket patterns

All under a shared tmpfs at `/run/mdbus`, owned by uid 10001.

| Endpoint | Pattern | Bind | Connect | Loss policy |
|---|---|---|---|---|
| `ingest` | PULL ← PUSH | tickbus | uplinks | lossy: `send(DONTWAIT)`, drop on `zmq.Again`, counter |
| `ticks.w` | PUB → SUB | tickbus | writer | high `SNDHWM`, `bus_seq_gaps` telemetry |
| `ticks.s` | PUB → SUB | tickbus | stream | lower HWM; the display may drop freely |
| `raw` | PUB → SUB | tickbus | future Rust engine | drop-tolerant |
| `receipts` | PUB → SUB | writer | stream, control | tiny |
| `health` | PUB → SUB | every service | control | 1 Hz |
| `ctl-<svc>` | REP ← REQ | each service | control | status, reload-universe, drain, stop |

Two separate PUB endpoints rather than one shared topic, so the writer and the display can be
tuned independently — the writer gets depth of buffer, the display gets freshness. If
measurement ever shows drops on `ticks.w`, the escape hatch is PUSH/PULL to the writer with a
bounded ring in the bus; start with PUB and monitor rather than building the complex thing
first.

Four ZMQ rules from `docs/10-architecture/tick-bus-and-algo-engine.md` still hold, and one
is added:

1. **Sockets are not thread-safe.** `on_message` fires on the KiteTicker thread — create that
   PUSH socket on that thread and touch it nowhere else. A `Context` may be shared; a socket
   may not.
2. **Never block on send in an uplink.** `DONTWAIT`, handle `zmq.Again`, count the drop.
3. **Subscribers cannot interpret ticks without the instrument map.** `SESSION_HEADER`
   republished at start and every N seconds.
4. **Every message carries `seq` and `publish_ns`.** PUB drops silently; a sequence gap is the
   only evidence a subscriber gets.
5. **Slow joiner.** A SUB can miss messages published between `connect` and subscription
   completion. Consumers must wait for a `SESSION_HEADER` before building their first frame,
   and must not treat pre-header ticks as a gap.

`pyzmq` is not currently a dependency (`backend/pyproject.toml:12`). Pin it explicitly.

---

## 6. Uplink services

Uplinks are token consumers, not authentication clients. They receive no API secret, broker
password, TOTP seed, or Calspread passcode. Each starts from the same protected session written
by `md-control` and authenticates its market-data connection with
`api_key + access_token`. Restarting an uplink always reuses that session unless `md-control`
has announced a newly validated token version after an administrator relogin at Calspread.

### 6.1 `md-uplink-ws`, sharded

Split 2,067 tokens across two sockets; Kite allows three per API key
(`BROKER_MAX_CONNECTIONS = 3`, `config.py:320`).

| Shard | Contents | Tokens | Headroom to 2,700 safe limit |
|---|---|---|---|
| A | 6 index chains + 6 spots + India VIX | ~1,219 | 1,481 |
| B | stock matrix + index F&O | ~858 | 1,842 |

Two processes, two twisted reactors, two failure domains. A dead shard degrades one domain
and `stale_artifacts` names it precisely. `SubscriptionPlan.shards()` already computes this
and has no caller.

Both shards use the same API key and same stable Kite-issued, Calspread-held access token. Sharding creates
independent transport/reactor failure domains; it does not create independent Kite sessions.

Two fixes belong here. `_on_connect` currently catches a `subscribe`/`set_mode` failure, logs
it, and leaves a connected socket carrying no data (`ticker.py:104`) — that must report
unhealthy. And running each shard in its own process finally answers the question
`docs/30-live-capture/live-data-pipeline.md` leaves deliberately open: whether an in-process
`reconnect()` can work under kiteconnect 5.x's process-global twisted reactor. A fresh
process gets a fresh reactor, so the question stops mattering.

### 6.2 `md-uplink-rest`, continuous warm standby

**Poll continuously, not on demand.** This is the design decision that makes the bus-first
topology work without a coordinator.

The REST poller uses the same API key and access token as the WS shards. Its continuous quote
polling is a market-data warm standby and must not be confused with polling Calspread for
credentials.

Nothing else consumes `/quote` except one bootstrap ATM seed (`bootstrap.py:104`), so the
1 req/s budget is otherwise idle. Polling always costs nothing we are using and buys three
things: no activation handshake (the REST poller never needs to be told anything, and the bus
switches sources with zero coordination), instant failover instead of a cold sweep, and
continuous cross-validation of WS values against an independent path.

Hard constraint from the [Kite docs](https://kite.trade/docs/connect/v3/market-quotes/) and
[rate limits](https://kite.trade/docs/connect/v3/exceptions/): `/quote` returns full depth,
OI, and OHLC for **500 instruments per call** at **1 req/s**. Aggregate ceiling is therefore
**500 full-fidelity instruments per second**, and 2,067 tokens take `ceil(2067/500) = 5`
calls, a **5-second full sweep**. REST cannot replicate a 1 Hz feed for the whole universe;
the fallback is tiered and the runbook must say so.

| Tier | Contents | Instruments | Calls | Writable |
|---|---|---|---|---|
| T0 | 6 index spots + VIX + ATM±5 strikes × 6 indices | ~139 | 1 | yes |
| T1 | remaining chain strikes | ~1,080 | 3 | yes |
| T2 | stock matrix + index F&O | ~858 | 2 | yes |
| T3 | whole universe via `/quote/ltp` | 2,067 | 3 | **no — display only** |

`/quote/ltp` returns 1,000 instruments per call but no depth, OI, or volume. A frame with a
real LTP and zeroed depth is precisely the fabrication invariant 2 forbids, so `REST_LTP`
ticks carry `flags |= LTP_ONLY` and the writer rejects them outright. They exist to keep the
display alive and to prove the account still has a working uplink.

Weighted round-robin over the 1 req/s budget using `aiolimiter` (already a dependency): T0 in
one slot of every three (≈3 s refresh), T1 and T2 sharing the rest (≈6–9 s). The poller yields
its budget during bootstrap so the ATM seed is not starved.

Field parity is achievable — `/quote` is a superset of WS `full` mode for every column the BIN
layout stores. One mechanical requirement: `/quote` is keyed by `exchange:tradingsymbol`, so
the poller needs a token → symbol reverse map, which the instrument dumps already contain.

### 6.3 What this actually recovers

| Scenario | Today | This design |
|---|---|---|
| One WS socket dies | 60 s detect + 25–30 s full-pipeline restart, all domains lost | bus switches that shard's tokens to REST in 5 s; other shard untouched; shard restarts in 2–5 s |
| Whole WS uplink dies | as above | all tokens on REST within 5 s, writes continue flagged `DEGRADED` |
| `md-tickbus` dies | n/a | <1 s restart, arbitration state rebuilt within one staleness window |
| `md-writer` dies | 25–30 s, market state lost too | 1–2 s, appends resume, no frame ever crossed a socket |
| `md-stream` dies | 25–30 s, archive stops too | archive completely unaffected |
| A browser tab opens | +21 ms/s on the capture path | zero effect on capture |
| Calspread is unavailable while the cached token is valid | startup/recovery wording is ambiguous | capture and uplink restarts continue from the protected cache; alert `session_source_unreachable` |
| An uplink reports typed auth rejection for active revision N | one endpoint or a delayed error can poison the shared credential | control fences on revision N and independently confirms rejection with Kite; only then stop all Kite calls using N, block the runtime projection, and enter `AUTH_BLOCKED` |
| Admin relogs in through Calspread | no explicit handoff contract | control detects a different token internally, validates it, atomically advances the durable revision/runtime projection, announces only the revision, and waits for uplink activation acknowledgements |
| A different Calspread candidate fails validation | bad candidate can replace the last usable credential | keep the current `READY` or `AUTH_BLOCKED` revision unchanged; never project it or advance revision |

**The hole shrinks; it does not vanish.** When the bus switches token `T` to REST, the
freshest REST value may be up to 5 s old (T1/T2 cadence up to 9 s). Its `exchange_ts_ms` is
stamped honestly, so the writer's freshness check may still suppress it. Expected per-artifact
outage cost falls from ~85–90 s (60 s detection + 25–30 s restart) to roughly **5–10 s** —
an order of magnitude, not zero. Claiming zero would be the same category of error as the
telemetry that once reported a healthy resumed session as 100% lost.

---

## 7. `md-writer` — builds and persists

The writer is now a full consumer, not a dumb sink: it maintains its own `IndexTable` /
`StockMatrix` / `IndexFnoMatrix`, runs its own 1 Hz grid, builds frames, gates them by segment
phase, and appends.

### 7.1 Internal pipeline

```
SUB ticks.w  →  apply_ticks()      [sync, NO await]   ← invariant 1, enforced here
              ↓
   1 Hz grid  →  snapshot(ts)      [sync, NO await]   ← invariant 1, enforced here
              ↓
              per artifact: phase × freshness gate
              ↓
              encode_*_frame()  →  write → flush → fsync
              ↓
              PUB receipt {artifact, grid_ts, seq, digest, written}
```

Everything after `apply_ticks` stays inside one process, so there is no frame-durability
problem to solve. That is the whole payoff of moving frame construction downstream.

### 7.2 Per-segment adaptive sessions

This is the fix for F1. Extend `ops/sessions.py` from two sessions to four and from four
phases to six. `MarketSession` already answers per-artifact
(`is_capture_expected_at`, `is_stale_armed_at`, `scheduled_seconds`); today
`bootstrap.py:198` assigns every artifact to `SESSION_EQUITY_DERIV` and the engine consumes
only the aggregate OR-gates (`bootstrap.py:257`).

| Segment | Artifacts | Phases (IST) |
|---|---|---|
| `equity_deriv` | index option chains, stock futures legs, index F&O futures legs | PRE_OPEN 09:00–09:15 · OPEN 09:15–**15:40** · CLOSED |
| `equity_cash_fno` | STOCKS spot leg | PRE_OPEN 09:00–09:15 · OPEN 09:15–**15:15** · AUCTION 15:15–15:35 · POST_CLOSE 15:50–16:00 · CLOSED |
| `index_spot` | 6 index spot tokens + India VIX | PRE_OPEN 09:00–09:15 · OPEN 09:15–**15:15** · AUCTION 15:15–15:35 · CLOSED |
| `equity_cash_other` | not captured today; reserved | OPEN 09:15–15:30 |

| Phase | `capture_expected` | `stale_armed` | Write rule | Counts toward loss |
|---|---|---|---|---|
| BOOTSTRAP | no | no | never | no (`unscheduled_seconds`) |
| PRE_OPEN | configurable, default no | **no** | only if `capture_pre_open` | no |
| OPEN | yes | yes, after `stale_arm_delay_s` | 1 Hz unless stale | yes |
| **AUCTION** | yes | **no** | **event-driven: only when the content digest changes** | no — separate `auction_seconds` bucket |
| POST_CLOSE | configurable, default no | no | event-driven if enabled | no |
| CLOSED | no | no | never | no |

`AUCTION` is the key addition. During CAS there is no continuous order matching, so a 1 Hz
grid of unchanged values is exactly the fabrication invariant 2 forbids — yet 15:30–15:35 is
where the official close is discovered and must not be dropped. Event-driven writing inside
AUCTION captures every indicative update and the final print without inventing anything.
Timestamps stay on the 1 Hz grid so `ops/completeness.py` reconciliation still works; what
changes is that AUCTION seconds are not *owed*, so a sparse AUCTION window is correct rather
than lossy.

Gate, per artifact per grid second:

```
phase = segment_of(artifact).phase_at(grid_ts)
if phase in (BOOTSTRAP, CLOSED):                    skip → unscheduled_seconds += 1
elif phase in (PRE_OPEN, POST_CLOSE) and not enabled(phase):
                                                    skip → unscheduled_seconds += 1
elif any contributing tick has flags & LTP_ONLY:    skip → hard reject (never writable)
elif phase == AUCTION:
     digest changed ? append : skip → auction_idle_seconds += 1
elif freshness.is_stale(grid_ts):                   skip → stale_seconds += 1
else:                                               append
```

### 7.3 Also owned by the writer

- **File lifecycle**: path resolution, header-once via `st_size > 0` (`writer.py:162`), and
  `scan_frames` on startup for authoritative resume state — count, first/last timestamp,
  truncated flag — at ~0.4–0.5 s for a full day.
- **File segmenting** (fixes F7): if an artifact's header identity changes mid-day — strike
  vector rebuilt, board composition changed — roll to `<date>.p2.bin` rather than append a
  frame whose `N` disagrees with the header. Latent today because no intra-day chain rebuild
  exists; required if the writer's tables ever re-centre a chain. Readers must learn to
  enumerate `<date>*.bin` in order.
- **Bounded internal queue** (fixes F7): replace the unbounded `queue.Queue`
  (`writer_thread.py:53`) with an explicit HWM. Since frames are built in-process, overflow
  means the disk is the bottleneck — the correct response is to stop consuming from
  `ticks.w` and let `bus_seq_gaps` record it, never to drop a built frame.
- **Provenance sidecar**: `MARKET_DATA/<artifact>/<date>.provenance.jsonl`, run-length encoded
  — `{"from_ms":…,"to_ms":…,"source":"REST_FULL","tier":"T1","cadence_ms":6000}`. Zero `.bin`
  format change, so deployed readers keep working and same-day files stay appendable. Schema
  v2 with per-frame `source`/`flags` bytes is the cleaner endgame but touches `layout.py`, all
  three writers, all three readers, `scan.py`, and every fixture.
- **Write receipts**: PUB `{artifact, grid_ts, seq, digest, written}` on `receipts`. See §8.2.
- **Completeness ledger**: per-segment scheduled grids plus the new bucket. The reconciliation
  identity becomes
  `stale_feed + downtime + write_path + auction_idle + unclassified == missing_seconds`.
- **EOD handoff**: signal `md-control` when the last segment closes and all files are drained,
  so compression runs at 15:40 (or 16:00 with post-close) rather than 15:30.

Keep zstd compression in `md-control`, not the writer: it is a 6-thread GIL-releasing burst
over ~8.9 GB, and running it in the writer would contend with fsync latency on the same
volume.

### 7.4 Config surface

```dotenv
EQUITY_DERIV_OPEN=09:15
EQUITY_DERIV_CLOSE=15:40

EQUITY_CASH_FNO_OPEN=09:15
EQUITY_CASH_FNO_CLOSE=15:15
EQUITY_CASH_FNO_AUCTION_START=15:15
EQUITY_CASH_FNO_AUCTION_END=15:35
EQUITY_CASH_FNO_POSTCLOSE_START=15:50
EQUITY_CASH_FNO_POSTCLOSE_END=16:00
EQUITY_CASH_FNO_CAPTURE_POSTCLOSE=false

INDEX_SPOT_OPEN=09:15
INDEX_SPOT_CLOSE=15:15
INDEX_SPOT_AUCTION_START=15:15
INDEX_SPOT_AUCTION_END=15:35

PREOPEN_START=09:00
PREOPEN_END=09:15          # matching moves to 09:10-09:12 from 2026-09-07
CAPTURE_PREOPEN=false

CAPTURE_START_TIME=09:12
CAPTURE_END_TIME=15:42     # 16:02 if post-close capture is enabled
```

Two validation fixes: `_validate_daily_schedule` (`config.py:502`) must assert the capture
window covers every enabled segment's latest close, and `MARKET_OPEN`/`MARKET_CLOSE` should
be demoted to a display-only market phase for `/api/status` rather than a silent fallback for
every segment. That silent fallback is what produced today's identical 09:00–15:30 windows.

---

## 8. `md-stream` — Greeks and browser delivery

### 8.1 Internal pipeline

```
SUB ticks.s  →  apply_ticks()   [sync, NO await]   ← invariant 1, enforced here too
              ↓
 per-topic grid → snapshot(ts)  [sync, NO await]
              ↓
              reconstruct_greeks + reconstruct_chain_metrics
              ↓
              encode ({type, payload, meta})  →  ConnectionManager  →  browser
```

Unchanged on the wire: the `{type, payload, meta}` envelope, the topic-per-URL subscription
model (`ws/routes.py:79`), and origin-only auth. The frontend needs no changes on day one.

Move `_prev_oi` (which drives `change_in_oi`, `broadcaster.py:228`) and `_index_headers` here.
Both are per-instance mutable state, so **`md-stream` must stay single-replica** until
`change_in_oi` is derived from sequence-adjacent frames instead of instance state.

### 8.2 Independent cadence

The freedom this topology creates, and the reason to prefer it. Suggested starting point:

| Topic | Cadence | Rationale |
|---|---|---|
| `market-data` (chains + headers) | 2–4 Hz | Greeks are 16 ms, ~2 ms with `py_vollib_vectorized`. Cheapest topic to accelerate and the one a trader watches. |
| `stocks` (StockBoard + IndexFnoBoard) | 1 Hz, or on-demand only | 310 KiB JSON → 119 KiB deflated, ~15.8 ms deflate **per connection**. The dominant cost. |
| `capture-status` | 1 Hz | telemetry |
| `session` | 1 Hz | heartbeat keepalive for the staleness dot |

Then take the wins the existing analysis already ranked, in payoff order: drop or lower
deflate on `stocks`; suppress the stock board entirely when no client has that page open (free
— the archive is written independently); ship binary using the existing `.bin` layout and a
browser `DataView`; and activate `OptionGridDelta` / `KEYFRAME_INTERVAL`, which are
implemented and dead (`ws/protocol.py:40,147`).

Raising the chain cadence above 1 Hz is only meaningful if ticks actually arrive faster than
1 Hz per instrument. Measure inter-tick intervals per token from the raw corpus before
choosing a number; 4 Hz on a feed that updates every 800 ms is 4× the CPU for no new
information.

### 8.3 Keeping the display honest about the archive

Independent frame construction means the display and the archive could disagree. That matters
more than it looks: `/monitor` is the primary incident tool, and the 2026-08 diagnosis was
only possible because telemetry and disk agreed.

Fix it without chaining the data path. The writer PUBs a receipt per artifact per grid second
— `{artifact, grid_ts, seq, digest, written}`, a few dozen bytes — and `md-stream` SUBs it.
The streamer can then badge every second by what actually reached disk: written, suppressed
stale, AUCTION-idle, unscheduled, or digest-mismatched. `md-control` compares the two streams
and raises `display_archive_divergence` when digests differ for the same `grid_ts`.

This preserves the parallel topology, costs ~40 B/s, and gives something today's monolith does
not have: an explicit, measurable statement that the display matches the archive, rather than
an assumption inherited from shared memory.

Add `provenance` to `meta` so the UI badges REST-filled, `DEGRADED`, and AUCTION seconds.
Silently rendering degraded data as normal is how the earlier incidents stayed invisible for
three days.

Correct `docs/50-frontend/websocket-protocol.md` and `frontend.md` per F8 before anyone builds
against them: they claim Greeks are absent from the feed and that `StockBoard` excludes depth,
and both are false.

`md-stream` must not be published on a public interface. Origin checks are not authentication,
as `docs/60-operations/vps-docker-deployment.md` states explicitly.

---

## 9. `md-control` and state ownership

`md-control` keeps FastAPI as it is today, but its authentication role is precisely bounded:
existing-session retrieval from Calspread, Kite validation, local session persistence,
`DailyAutomationService`, EOD compression, `/api/status`, `/api/stats`, `/health`, `/monitor`,
and the release-maintenance lease.

It does **not** log in to Kite, exchange request tokens, refresh access tokens, or collect
broker credentials/TOTP. Only Calspread performs those operations. `md-control` synchronizes
an external session that already exists.

Session synchronization is fetch-before-replace and fingerprint-aware. A retrieved token is
validated before it can become active. The active session is never deleted merely because
Calspread is unavailable or returns the same token. If Kite explicitly rejects the active
token, control marks that token fingerprint rejected and enters `AUTH_BLOCKED`; repeated reads
of the same Calspread token leave the system blocked. A different, validated token is the only
event that clears the state. In operational terms, that means an administrator relogged in at
Calspread.

Authentication reports are revision-fenced. Each uplink includes its
`active_credential_revision` with a typed auth failure. `md-control` ignores delayed failures
for older revisions and independently validates the active API-key/token pair before converting
an endpoint report into a global credential block. A confirmed rejection blocks every WS/REST
uplink using that shared revision; an unconfirmed or shard-specific failure remains transport
recovery.

Credential revision is durable and monotonic across control restarts. A same-token lookup may
advance observation metadata but cannot rewrite the secret projection or increment revision.
For a validated change, control atomically commits durable N+1 first, atomically replaces the
runtime projection second, then announces only its opaque revision. Each uplink reopens the
file, verifies the revision, builds a replacement client, and acknowledges activation. The two
files live on separate volumes, so this is an ordered crash-safe protocol, not one atomic
cross-volume transaction. Failed candidates never replace the durable record.

The durable record is the source of truth. On every control startup, `READY(N)` reconstructs
projection N if necessary; `AUTH_BLOCKED(N)`, `NO_TOKEN`, and `WAITING_FOR_ADMIN` remove any
usable projection before readiness is declared. Blocking stops and acknowledges all uplinks
using N, atomically persists `AUTH_BLOCKED(N)`, then revokes the runtime projection. A crash at
any step is repaired from durable state before an uplink can become ready.

Multi-process writes to `_state/` are a new corruption risk that the existing atomic-write
helpers do not solve by themselves. Fix it with exclusive ownership:

| Path | Sole writer | Readers |
|---|---|---|
| `/var/lib/md-control/broker-credential.json` | `md-control` | control only; private durable mode-`0600` record containing the secret access token plus `revision`, internal fingerprint, `retrieved_at`, `validated_at`, and status; atomically replaced and redacted from errors/logs |
| `/run/mdsession/current.json` | `md-control` | WS/REST uplinks only, read-only; minimal runtime credential projection |
| `_state/session-<date>.json` | `md-control` | control/API; trading-date and risk-free-rate metadata, not credential authority |
| `_state/release-maintenance.json` | `md-control` | all |
| `_state/stats/escalations-<date>.json` | `md-control` | all |
| `_state/stats/capture-<date>.json` | `md-writer` | control, API |
| `_state/stats/completeness-<date>.json` | `md-writer` | control, API |
| `_state/stats/stream-<date>.json` | `md-stream` | control, API |
| `_state/stats/compression-history.jsonl` | `md-control` | API |
| `_state/stats/session-history.jsonl` | `md-control` | API |
| `MARKET_DATA/**/*.bin`, `*.provenance.jsonl` | `md-writer` | readers, EOD |
| `ARCHIVE_DATA_PATH/**` | `md-control` (EOD) | readers |

`_state/` remains `0o700` with `0o600` files (`session.py:94`), but it is not mounted into every
service merely because processes share uid 10001. Mount isolation is the primary boundary:
only `md-control` mounts durable credential state; only WS/REST uplinks mount the read-only
runtime projection. Writer, stream, tickbus, and browser-facing services receive neither the
access token nor the Calspread passcode.

Two existing bugs to fix here, both from
`docs/60-operations/review-2026-07-28-streaming-and-dual-uplink.md`:
`session-history.jsonl` rows are **replaced** per trading date and written **only on a clean
stop**, so a design where services restart several times a day can overwrite or lose the day's
audit row. With the split, write the row on segment close rather than on process stop.

---

## 10. Deployment

Seven or eight backend containers plus the existing frontend (eight or nine total), instead of
today's backend/frontend pair. Extend `compose.yaml`'s existing `x-service-security` anchor —
`init: true`, `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, uid 10001, json-file
logging 10 MB × 3 — to every new service unchanged.

- **Shared IPC volume.** `ipc://` needs a real path visible to the relevant containers: a named volume
  or host tmpfs at `/run/mdbus`, mounted `rw` into all services, owned 10001:10001,
  `noexec,nosuid`. Root FS stays read-only; each service keeps its own 64 MB `/tmp` tmpfs
  (needed because `HOME=/tmp` in the Dockerfile).
- **Credential volumes.** Give `md-control` a private durable volume at
  `/var/lib/md-control` and a separate runtime projection volume at `/run/mdsession`. The
  runtime volume is `rw` only in control, `ro` only in WS/REST uplinks, and absent everywhere
  else. The Calspread passcode exists only in `md-control`. Never send the access token through
  `SESSION_HEADER`, PUB health, REQ/REP commands, logs, or browser APIs.
- **Volume scoping.** `MARKET_DATA_PATH` is `rw` only for `md-writer` and `md-control`,
  read-only elsewhere. `ARCHIVE_DATA_PATH` is `rw` only for `md-control`. Keep the existing
  `create_host_path: false` and identical host/container paths.
- **Startup order.** Start `md-control` for liveness, then `md-tickbus` (binds `ingest`,
  `ticks.*`) and consumers. Uplinks wait for credential readiness before connecting. An
  `AUTH_BLOCKED` control process is live but the market-data plane is unready; the status/API
  surface must remain available to explain the required Calspread administrator action.
- **Grace periods.** Fix the existing mismatch (F8): `compose.yaml` sets
  `stop_grace_period: 30s` while `api/capture.py:34` allows `STOP_TIMEOUT_S = 45.0`. Docker
  SIGKILLs first. `md-writer` needs a grace period exceeding its drain budget; `md-stream`
  and the uplinks can drop to a few seconds since neither holds unflushed data.
- **Supervision stays self-exit → `restart: unless-stopped`.** An `unhealthy` healthcheck does
  not restart a container; only PID 1 exiting does. Each service keeps its own fatal handler.
  The per-date escalation ledger moves to `md-control`, now the only service that must never be
  in a restart loop.
- **Health contracts.** `/health/live` reports the functioning control process/API and stays
  200 in `WAITING_FOR_ADMIN`/`AUTH_BLOCKED`; Compose supervision and restart policy use it so
  admin action cannot create a restart loop. `/health/ready` requires a `READY` credential and
  required services on the active revision. Preserve aggregate `/health` as a compatibility
  readiness endpoint. `deploy.sh` and release gates use readiness, or a deploy could pass with
  writer/uplinks unavailable; frontend `/login` remains an operator surface, not a liveness
  probe.
- **Resource envelope is unverified.** No CPU/RAM figures for the VPS exist in any doc; only
  storage (~8.9 GB/day raw, ~1.5–2.5 GB/day compressed, ~1 GB/day sustainable). Six CPython
  interpreters is roughly 0.5–0.8 GB RSS before market data, and duplicated tables add only
  2 × 1.2 MB. **Measure the VPS before committing.** If memory is tight, co-host `md-tickbus`
  with `md-control` — it is the smallest process and neither holds market state.
- **Network.** The dual-ISP silent-failover risk in
  `docs/60-operations/review-2026-07-28-streaming-and-dual-uplink.md` §2.6 is unchanged:
  capture survives an interface failover with no alert. Now that a service exists whose only
  job is the uplink, add an egress-interface field to its health message.

---

## 11. Observability

Each service PUBs a 1 Hz `health` message; `md-control` aggregates into `/health`,
`/api/status`, and `/api/stats` so the existing contracts and `/monitor` keep working.

| Service | Key fields |
|---|---|
| `md-uplink-ws` | `connected`, `transport_age_ms`, `subscribed_tokens`, `subscribe_failed`, `bus_send_dropped`, `egress_interface`, `active_credential_revision` |
| `md-uplink-rest` | `rest_calls_used`, `rest_budget_pct`, `sweep_age_ms_by_tier`, `http_errors`, `429_count`, `active_credential_revision` |
| `md-tickbus` | `arbiter_state_by_artifact`, `source_by_artifact`, `tokens_on_rest`, `ticks_in/out_per_s`, `deduped_per_s`, `seq`, `ingest_dropped` |
| `md-writer` | `frames_written`, `writer_pending`, `fsync_p99_ms`, `phase_by_segment`, `stale_seconds`, `auction_idle_seconds`, `unscheduled_seconds`, `file_segments`, `bus_seq_gaps` |
| `md-stream` | `clients_by_topic`, `cadence_hz_by_topic`, `greeks_ms`, `iv_solve_failures`, `encode_ms`, `deflate_ms`, `bus_seq_gaps`, `digest_mismatches` |
| `md-control` | `credential_status` (`NO_TOKEN`, `WAITING_FOR_ADMIN`, `READY`, `AUTH_BLOCKED`), `credential_source_status` (`UNKNOWN`, `AVAILABLE`, `UNAUTHENTICATED`, `UNREACHABLE`, `INVALID_RESPONSE`), opaque `credential_revision`, `calspread_last_success_ms`, `auth_blocked_since`, `operator_action_required`, existing automation/compression fields, `services_up`, `services_degraded`, `display_archive_divergence` |

Loss and fidelity stay separate metrics: `ws_seconds`, `rest_filled_seconds`,
`degraded_seconds`, `blind_seconds`. `degraded_seconds` is **not** loss — conflating the two
would repeat the reporting bug that made a healthy resumed session report 100% loss.

Keep the existing honesty rules: `pipeline_ms` is server-measured deliberately (comparing to
browser `Date.now()` reports clock skew, not latency); `ticks_per_sec` is a trailing rate,
never a lifetime average; an unknown baseline reports 0 rather than a fabricated `23_400`.

---

## 12. Invariants

Preserved unchanged: 2 (no fabricated frames), 3 (sequence gaps as audit trail),
5 (header-once), 6 (schedule-based denominators), 7 (frontend never backpressures — now
structurally guaranteed, since the display is on a different process and a different socket),
8 (Greeks never persisted).

Changed, each with a required test change:

| Change | Why | Test |
|---|---|---|
| **Invariant 1 is now enforced in two processes.** `apply_ticks` and `snapshot` must stay synchronous and non-yielding in both `md-writer` and `md-stream`. | Each consumer owns its own tables; a stray `await` in either produces datasets that disagree about the instant they represent. | Add a static check (AST or `inspect`) asserting neither function is a coroutine in either service. The current guarantee is a code-review rule; make it mechanical, since it now applies twice. |
| **Invariant 4 becomes per-segment.** Frame alignment holds within a segment, not across all files. | After 15:15, index and stock-spot files legitimately have sparser timestamps than derivatives files. Global alignment is no longer physically possible. | `test_stale_write_suppression_e2e.py:162` asserts `list(reader.timestamps) == timestamps` across the index and stock files. Rewrite as per-segment. |
| **Display/archive agreement moves from structural to verified.** | Two independent builders instead of one snapshot. | New test: same tick script into both consumers must yield identical digests per `grid_ts`; and the receipt topic must flag an injected divergence. |
| Suppression stays producer-side — but the producer is now the writer. | Sequence is stamped by the writer's own tables at build time, so invariant 3 holds unchanged. | `test_data_loss_telemetry.py` engine-level suppression tests move with the code; keep the `suppress_stale_writes=False` opt-out. |

One control-plane invariant is added: **retrieval is idempotent**. Observing the same Calspread
token cannot increment credential revision, restart an uplink, rewrite issuance history, or be
reported as a refresh. `retrieved_at`/`validated_at` record downloader observations, not the
unknown time when Kite issued the token.

Required authentication tests make this mechanical:

- same-token lookup does not advance revision, rewrite projection, or restart an uplink;
- restart while Calspread is unavailable reconstructs projection N from durable `READY(N)`;
- durable `AUTH_BLOCKED(N)` never re-projects its token after a crash between any ordered step;
- delayed auth reports for an older revision are ignored, and unconfirmed rejection remains a
  transport failure;
- a different invalid candidate preserves the current `READY` or `AUTH_BLOCKED` revision;
- valid N+1 handoff waits for every required uplink activation acknowledgement;
- token, passcode, and fingerprint never appear in health, logs, ZMQ, browser APIs, or
  non-uplink mounts.

Do the invariant-4 change **first, in the monolith**, before extracting anything. It is a
behavioural change to the archive contract, and mixing it with a topology change makes any
regression impossible to attribute.

Tests that must keep passing verbatim throughout: `test_feed_health.py` (the classifier is
pure and moves as-is), `test_writer_health.py` (drain-proof stop, sticky failure),
`test_restart_resume.py` (append, no second header, `scan_frames` agrees with the reader, torn
trailing frame tolerated), and the whole-day sequence in `test_session_lifecycle_drill.py`
extended with AUCTION cases.

---

## 13. Migration phases

Each phase ships independently and is reversible. No phase leaves the archive in a format
older readers cannot read.

**Phase 0 — correctness in the monolith, no new services.** Highest value per unit of risk;
fixes the active daily data loss in F1.

- Decouple feed recovery from credential state: stale/disconnected feeds reuse the same token
  and perform zero Calspread calls. Introduce `AUTH_BLOCKED` for explicit Kite auth rejection;
  the same rejected token remains blocked until an administrator changes it in Calspread.
- Separate durable broker credential metadata from trading-date/risk-free-rate session metadata;
  rename refresh metrics and operations to observed token changes/session synchronization.
- Add `AUCTION` and the four segments to `ops/sessions.py`; assign artifacts per segment
  instead of the blanket `SESSION_EQUITY_DERIV` (`bootstrap.py:198`).
- Replace the aggregate `any_capture_expected` / `any_stale_armed` gates
  (`bootstrap.py:257`) with per-artifact gating in `capture_snapshot`.
- Extend the capture window to 15:42; fix `_validate_daily_schedule`; move the EOD trigger to
  the last segment close.
- Rewrite the invariant-4 test as per-segment; add AUCTION cases to the lifecycle drill.
- Persist `exchange_timestamp` (open question C).
- Update `failure-modes.md`, `storage-layout.md`, `websocket-protocol.md`, `frontend.md` per
  F8; fix the 30 s / 45 s grace mismatch.

**Phase 1 — `NormTick` and the bus wire format, still in-process.** Add `pyzmq`. Define
`NormTick` and `BusHeader`. Refactor `TickerBridge` so it emits `NormTick` arrays instead of
Kite dicts, and make `apply_ticks` consume `NormTick`. Add a PUB tap that publishes what the
in-process pipeline is already consuming; nothing subscribes yet. This validates the wire
format against real frames and is trivially revertible.

**Phase 2 — extract `md-stream`.** The safest extraction: never-persisted output, visible
failure mode, and the archive is untouched if it breaks. It is also the one that immediately
removes 47–90 ms/s from the capture path. Run in shadow mode first, diffing its frames against
the in-process broadcaster over a full session. Fold in Greeks; swap `py_vollib_vectorized`
behind a convention version flag; then raise the chain cadence once inter-tick intervals are
measured.

**Phase 3 — extract `md-writer`.** Move the per-segment gating from Phase 0 into it, plus file
segmenting, the bounded queue, provenance sidecars, and the receipt topic. Dual-write into a
scratch root and compare byte-for-byte against the in-process writer for a full session before
switching. No ACK protocol and no WAL are needed — frames never cross a socket.

**Phase 4 — insert `md-tickbus` and extract `md-uplink-ws`.** Wire
`SubscriptionPlan.shards()`; fix the swallowed subscribe failure (`ticker.py:104`). With
uplinks in their own processes, the twisted-reactor reconnect question in
`live-data-pipeline.md` becomes moot. Arbitration exists but has one source, so it is a
pass-through until Phase 5. Give uplinks read-only access to the `md-control` session cache;
specifically, the minimal `/run/mdsession` projection rather than durable control state. Do not
copy direct-login or token-refresh code into them.

**Phase 5 — add `md-uplink-rest`.** Tiered `/quote` scheduler, continuous polling, and
per-token arbitration in the bus. Run in shadow mode first: poll, publish, and *discard*,
comparing REST-derived values against WS values for the same tokens and grid seconds. That
establishes field-level parity and measures real cadence under the 1 req/s ceiling before
anything REST-derived reaches disk.

**Phase 6 — finish `md-control`.** Aggregate health, per-service healthchecks, `deploy.sh`
gates, Calspread session synchronization and the `AUTH_BLOCKED` state machine, session-history
row on segment close. Remove the downloader's legacy direct Kite login/TOTP and token-refresh
responsibilities so the documented trust boundary is enforced. Retire `md-capture`
(`app/capture/run.py`) or reduce it to a dev harness — its market-hours watcher and EOD trigger
are duplicated three ways once this lands.

Run the reconnect drill from `docs/30-live-capture/live-data-pipeline.md` at the end of
Phase 4 and again at the end of Phase 5. It has never been run, so restart-first recovery has
never been validated live.

---

## 14. Risks and open questions

| Risk | Impact | Mitigation |
|---|---|---|
| **Display and archive diverge** — two independent builders | `/monitor` misleads during exactly the incident it exists to diagnose | Canonical `seq`/`grid_ts` from the bus; single-sourced arbitration; write-receipt topic; `display_archive_divergence` metric; a test asserting identical digests from identical input |
| **`md-tickbus` is the single inline stage** | its restart time is the pipeline's floor | No state beyond `last_seen_ns[token]`, no disk, no parsing, no network. Restart <1 s; arbitration rebuilds within one staleness window. Co-hostable with `md-control` if container count is a problem. |
| Invariant 1 must now hold in two processes | silent cross-dataset timestamp inconsistency | Make the no-`await` rule a mechanical test, not a review convention |
| Tick loss on the bus→writer hop | archive gaps | Same risk class as today's drop-oldest `asyncio.Queue`; monitor `bus_seq_gaps`; escape hatch is PUSH/PULL with a bounded ring |
| REST fallback presented as WS-equivalent | archive silently degrades | Provenance sidecar; `degraded_seconds` separate from loss; UI badge; runbook states the ≥5 s full-sweep floor |
| `REST_LTP` data reaching the writer | fabricated depth and OI in the archive | `LTP_ONLY` flag set at the uplink, hard-rejected in the writer gate |
| Continuous REST polling | burns the whole `/quote` budget permanently | Nothing else uses `/quote` except one bootstrap seed; poller yields during bootstrap. Revisit if a second consumer appears. |
| Calspread outage is mistaken for token expiry | a valid cached session is discarded and capture stops unnecessarily | Keep using a Kite-validated cached token; report source reachability separately from token validity |
| Re-fetching the same rejected token is called a refresh | infinite recovery loop with no possible credential change | Compare fingerprints internally; expose only an opaque credential revision; remain `AUTH_BLOCKED` until Calspread returns a different token created by admin relogin |
| Downloader retains broker login secrets | expands the blast radius and creates two competing session authorities | Target runtime holds no API secret, password, TOTP, or request-token exchange path; Calspread is sole authority |
| Duplicated bootstrap in two consumers | slower startup, two chances to disagree on the universe | `SESSION_HEADER` is authoritative and republished; consumers refuse to build frames before receiving one |
| Multi-process `_state/` writes | corrupted session or lease | Exclusive ownership table (§9); no shared-path writes |
| `md-stream` scaled beyond one replica | `change_in_oi` wrong | Single replica until `_prev_oi` derives from sequence-adjacent frames |
| Raising display cadence with no new information | 4× CPU for nothing | Measure per-token inter-tick intervals from the raw corpus first |
| `deploy.sh` health gate too narrow | deploy passes with a dead writer | Aggregate health into `/health` in the same phase that adds each service |

Open questions:

1. **Do CAS-window ticks actually reach the Kite WS feed for index spots and F&O-stock spots
   between 15:15 and 15:35, and with what cadence and fields?** The entire AUCTION design
   depends on this and it is not answerable from documentation. Needs one instrumented live
   session capturing raw payloads for a handful of tokens across 15:10–15:45.
2. **What is the real per-token inter-tick interval distribution?** Sets the ceiling on a
   useful display cadence, which is the main justification for this topology.
3. **Is post-close (15:50–16:00) cash data worth capturing?** Trades execute at the CAS
   closing price. Cheap as a phase, but extends the window 20 minutes and delays EOD.
4. **Does the writer ever need to re-centre an option chain intra-day?** If spot moves beyond
   ATM±50, coverage is silently lost today. If re-centring is wanted, file segmenting stops
   being latent and becomes required.
5. **Sidecar provenance or schema v2?** Sidecar is the safe first move, but if v2 is coming
   anyway, doing it once is cheaper than doing both.
6. **Should the uplink escalation deadline be shorter than the writer's?** A shard restart is
   2–5 s with REST covering the gap, so 60 s of waiting is now pure loss. Needs a measured
   shard restart time.
7. **What are the VPS CPU and RAM figures?** Blocks the container-count decision.
8. **What operator alert and acknowledgement should accompany `AUTH_BLOCKED`?** Recovery
   requires an administrator to relogin at Calspread; automation cannot resolve it by retrying.

---

## 15. Non-goals

- Order placement, execution, and risk. Out of scope for this repo
  (`docs/00-overview/overview-and-scope.md`); Kite's static-IP whitelist applies only to order
  placement.
- Computing or storing Greeks at capture time. Invariant 8 stands.
- Replacing the `.bin` format, the framing, or the integer-paise convention. `NormTick` aligns
  to the existing column order precisely so nothing downstream needs to translate.
- Adding a message broker, a database, or a queue product. ZMQ is brokerless; `md-tickbus`
  earns its place by owning arbitration, not by forwarding bytes. The archive is the database.
- An XPUB/XSUB proxy. Worth adding only if subscribers need an endpoint that survives
  publisher restarts, or a subscriber moves off-host and needs `tcp://`. Defer.
- msgpack, bincode, or Parquet on the wire. Standing decision in
  `docs/10-architecture/tech-stack-and-efficiency.md`.
- A second market-data vendor. Dual uplink here means WS + REST against the same broker.
- Direct Kite login, TOTP collection, request-token exchange, or access-token rotation in this
  downloader. Those are Calspread responsibilities.
- Browser operator authentication. Still out of scope; the deployment stays Tailscale-bound.
- Free-threaded CPython, or uvicorn with `--workers > 1` in any service that owns tables.
- Rewriting the frontend. The `{type, payload, meta}` envelope and topic model are preserved.

---

## Related

- [[live-data-pipeline]] — locked spec for stale-write suppression, three-signal feed health,
  restart-first recovery, and the open reconnect question
- [[tick-bus-and-algo-engine]] — the agreed ZMQ design, the four gotchas, the measured cost
  table, and the py_vollib rationale
- [[rust-ipc-zmq-plan]] — brokerless `ipc://` PUB/SUB decision and Docker socket sharing
- [[concurrency-and-gil]] — locked threading model; why threads not processes on the capture
  path
- [[bin-structure-spec]], [[bin-format]], [[storage-layout]] — the format `NormTick` aligns to
- [[failure-modes]] — needs the F8 update before it is trusted
- [[session-state]], [[config-and-env]] — `_state/` ownership and the env surface
- [[broker-session-authentication]] — canonical Calspread authority and `AUTH_BLOCKED` state
- [[live-capture-performance]] — measured headroom and the 2,067-token budget
- [[review-2026-07-28-streaming-and-dual-uplink]] — restart-loses-stats incident,
  session-history limits, silent ISP failover
- [[decisions-and-open-questions]] — open question C (`exchange_timestamp`), locked decisions
  #8 and #15
- `logs/progress-log.md` 2026-08-07 — root cause of the random feed death
