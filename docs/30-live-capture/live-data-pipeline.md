---
title: Live Data Pipeline
area: live-capture
type: spec
status: locked
tags: [area/live-capture, type/spec, status/locked]
up: "[[Live-Capture-MOC]]"
related: ["[[option-chain-selection]]", "[[stocks-capture]]", "[[live-capture-performance]]", "[[bin-structure-spec]]", "[[tech-stack-and-efficiency]]"]
---

# Live Data Pipeline

End-to-end flow for live capture. Mirrors `algo_engine` minus Greeks; adds the stock
matrix. Greeks are never computed at capture — reconstructed on read ([[bin-format]]).

## Stages

```
1. Bootstrap   fetch instrument dump (+ daily archive) + reference spot/VIX
2. Assemble    per index: ATM ± 50 empty table + token map ([[option-chain-selection]])
               stocks: CalSpread board + matrix ([[stocks-capture]])
3. Subscribe   open Kite WebSocket (KiteTicker), subscribe all tokens in "full" mode
4. Ingest      KiteTicker on_ticks → asyncio queue → decode (continuous)
5. Apply       write raw tick fields into the columnar table/matrix at token→index
6. Capture     ONCE PER SECOND (1 Hz): snapshot latest state → frame
7. Persist     enqueue frame to the per-file .bin writer ([[bin-structure-spec]])
               — SKIPPED while the feed is stale (see "Stale seconds are not written")
8. Broadcast   push slim frames + capture status to the frontend ([[websocket-protocol]])
```

## Bootstrap

- Load instrument masters for the needed exchanges (NFO for NSE index/stock options &
  futures, BFO for SENSEX, NSE for equity spots). Cache + daily archive ([[storage-layout]]).
- Resolve spot + VIX via a short WS read or a REST LTP call. Spot must be > 0.

## Subscription

- Universe ≈ 4 indices × ~202 option legs (~808) + ~200 stocks × (spot + 3 futures,
  ~800) ≈ **~1,600 tokens** — under Kite's 3,000/connection limit, so **one WS
  connection** suffices. Shard to a second connection only if the count grows.
- Subscribe then set mode `full` (delivers OI, OHLC, and 5-level depth).
- Depth retained: **indices L1**, **stocks L5** ([[depth-level-research]]).

## Ingest & apply

- Decode each tick via the `KiteTicker` callback (it parses the binary packet incl.
  5-level depth).
- Look up `instrument_token` in the token map:
  - `Option{side, idx}` → write raw fields into the CE/PE integer arrays at `idx` (L1).
  - stock leg → write into that stock's row in the matrix (L5 depth).
  - `Spot`/`Vix` → update the index scalars.
  - unknown token → increment an "unmatched" counter and ignore.
- **Raw only.** `change`, `change_in_oi`, IV, Greeks, and spreads are **not stored at
  all** (our own schema, [[bin-format]]) — reconstructed on read.

## Capture cadence — 1 Hz (locked)

Ticks are applied continuously; a **1-second timer** writes the *latest* state of each
table/matrix as one frame. Predictable volume, trivial CPU, wall-clock-aligned samples
(good for cross-instrument/arbitrage alignment), last-value-wins per second. See
[[live-capture-performance]].

## Stale seconds are not written

A grid second is persisted **only if the feed is fresh** — i.e. the batch content digest
(`last_price`, `volume_traded`, `oi`, `exchange_timestamp`, `last_trade_time`) changed
within `CAPTURE_STALE_SECONDS` (default 5). While stale, the frame is still built and
broadcast so the dashboard keeps showing the last board badged stale, but nothing is
enqueued to any writer.

Why absence beats duplication: the `.bin` layout is fixed-width, so a frame of duplicated
last-known values is byte-indistinguishable from a real one. Frame count, file size,
cadence and frame-integrity all keep reading perfect, and nothing in the archive marks
which prints never happened — unrecoverable, silent corruption. A missing second is
visible in the timestamps, counted as `stale_seconds`, surfaced as `data_loss_pct`, and
backfillable from the historical API.

Consequences to know:

- `captures`, `first_capture_ms` and `last_capture_ms` count **persisted** frames only.
  Elapsed time is tracked separately by `first_grid_ms` / `last_grid_ms`, which advance
  every grid second — otherwise a feed that dies at the open would report a zero-length
  span and therefore 0% loss.
- `sequence` advances once per grid second *built*, so an on-disk sequence step of N means
  N grid seconds elapsed. The file records its own holes and can be audited without the
  telemetry JSON (which a process restart can lose).
- Two loss figures follow from this: `session_loss_pct` (written vs *writable* seconds —
  gaps and write-path failures only) and `data_loss_pct` (written vs *all* elapsed
  seconds — the honest total, including stale).
- Opt out with `CAPTURE_SUPPRESS_STALE_WRITES=false` to restore the legacy write-anyway
  behaviour. Only useful for reproducing an old session's shape.

## Concurrency model (Python)

- **Ingest:** KiteTicker's threaded callback bridged into an `asyncio.Queue`.
- **Apply:** inline (cheap NumPy integer writes).
- **Capture timer:** a 1 Hz task snapshots every table/matrix → per-file writer queue +
  broadcast channel.
- **Writer:** a dedicated thread per file (blocking I/O); near-idle at 1 Hz.
- **Compression:** EOD zstd L17 in a pool, off the hot path ([[tech-stack-and-efficiency]]).

### Snapshot consistency (verified)

The broker callback never touches a table: `TickerBridge._on_ticks` only does
`loop.call_soon_threadsafe(self._enqueue, ticks)`. Tick application
(`CaptureEngine.apply_ticks`) and snapshot copying (`capture_snapshot`) are both plain
synchronous functions with no `await`, scheduled on the same event loop, so they cannot
interleave and a frame can never hold a half-applied batch. **No lock is used, and none is
needed** — the guarantee is event-loop ownership.

This property is load-bearing for cross-instrument analysis: one timestamp must mean one
consistent observation across every dataset. If either function ever becomes a coroutine,
or gains an `await` between building the index frames and the stock frame, the loop may
apply ticks mid-snapshot and produce datasets that disagree about the instant they
represent. Preserve the no-yield property or replace it with an explicit lock.

Writer threads only ever see the copies, so they never race the apply path.

## Measured cost (2026-08-07, `python -m tools.profile_capture`)

Measured at deployed scale — six option chains × 101 strikes, 208 F&O stocks × 4 legs with
L5 depth, six index-F&O rows × 4 legs — before deciding whether anything needs optimising.

| path | cost | notes |
|---|---|---|
| `apply_ticks`, 700 ticks | 3.59 ms | one second of production tick flow |
| `capture_snapshot`, all domains | **0.20 ms** | the only work bound to the 1 Hz grid = **0.02%** of the budget |
| — one index chain | 0.011 ms | ×6 |
| — stock matrix | 0.077 ms | |
| — index-F&O matrix | 0.060 ms | new |
| encode stock frame | 0.286 ms | on the writer thread |
| write + fsync, stock frame | 0.398 ms | on the writer thread |
| live table memory | 1.2 MB | whole live state |

Daily uncompressed growth: index 0.55 GB × 6, stocks 5.42 GB, **index-F&O 0.19 GB**, total
**8.92 GB/day**. Two extra indices add ~1.1 GB/day; the new domain itself adds 0.19 GB and
0.06 ms per grid second.

Subscription: **2,067 tokens** — computed from the 2026-08-07 masters, not estimated (six
chains × 202 options + six spots + shared VIX + 18 index futures + 830 stock tokens). That
is 633 below the 2,700 safe threshold and 69% of the broker's 3,000 hard limit, so one
connection still carries it. `test_the_six_index_universe_with_index_fno_fits_one_connection`
guards that arithmetic.

**Validated against production**: the 2026-08-07 session (four indices) recorded 23,493
captures and 7.99 GB on disk; the per-frame model predicted 7.86 GB for that frame count,
within ~2%.

**Conclusion: no optimisation is warranted.** The 1 Hz path uses 0.02% of its budget and
tick application ~0.4% of a core, so the implementation remains three orders of magnitude
from its limit even with six indices. Re-run the profiler before revisiting that — in
particular if strike windows widen, expiries are added, or depth levels increase, since the
stock matrix dominates both CPU and disk.

## Three separate clocks

Conflating these is what made a routine pre-open look like a dead feed, so they are
configured independently and nothing is hardcoded:

| concept | setting | on the deployment | governs |
|---|---|---|---|
| bootstrap | `BOOTSTRAP_TIME` | 08:55 | when the process may prepare (informational) |
| capture window | `CAPTURE_START_TIME` / `CAPTURE_END_TIME` | 09:10 – 15:30 | when the **process** runs: connect, subscribe, drive the grid |
| market session | `EQUITY_DERIV_OPEN` / `_CLOSE` (+ pre-open) | 09:15 – 15:30 | when a frame is **owed**: loss accounting and stale escalation |

The capture window opens five minutes early on purpose — the socket and subscriptions need
to be live before the first print. Those lead-in seconds are reported as `unscheduled` and
can never become data loss. Extending `CAPTURE_END_TIME` past the session close keeps a
post-close tail on the same terms. Both fall back to `MARKET_OPEN`/`MARKET_CLOSE` when
unset, and the expected-frame baseline comes from the **session**, not the capture window,
so day-progress agrees with the scheduled loss figures.

## Data domains

Three independent capture domains, each with its own file(s) and binary contract:

| domain | file | shape |
|---|---|---|
| per-index options | `INDICES/<INDEX>/<date>.bin` | one index, nearest expiry, ATM±50 strikes, L1 |
| stock F&O | `STOCKS/<date>.bin` | N stocks × 4 legs (spot + 3 futures), L5 depth |
| **index F&O** | `INDICES_FnO/<date>.bin` | N indices × 4 legs (spot + 3 futures), L5 depth |

Paths are relative to `MARKET_DATA_PATH` live and to `ARCHIVE_DATA_PATH` once compressed —
the EOD sweep preserves the live layout, so `INDICES_FnO/` appears unchanged under the
archive root without any separate wiring. On the deployment that is
`/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA/INDICES_FnO` and
`/srv/data/DATA_DOWNLOADER/ARCHIVE/INDICES_FnO`.

Coverage is all six indices — NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX —
driven entirely by `INDICES`, so the set is a configuration choice rather than anything the
domain hard-codes.

The index-F&O domain (`app/index_fno/`) is new and deliberately **not** folded into either
existing dataset. Index and stock derivatives are separate domains whose schemas must be
able to evolve apart, and merging them would have meant migrating historical stock data for
a change that has nothing to do with stocks. The stock F&O binary contract is untouched.

Why one consolidated file rather than six: index basis, calendar spreads, cross-index
relative value and lead/lag all compare two instruments **at the same instant**. One frame
therefore holds the whole index derivative universe for that second, and each index's spot
sits alongside its futures so the basis needs no cross-file join and no assumption that two
files line up. Coverage follows `INDICES`, so extending the supported index set is a
configuration change plus an `INDEX_CONFIGS` entry — nothing in the domain hard-codes which
indices exist.

Only raw state is persisted. Basis, synthetic futures, spreads, IV, Greeks and any signal
remain reconstruction-time calculations, so the archive stays usable when the maths changes.

Index futures were previously discarded rather than absent: `stocks.board.build_board`
collects every NFO `FUT` row and then drops those without a matching NSE `EQ` row, which is
exactly the index ones. `app/index_fno/board.py` is the counterpart that keeps them, reading
both NFO and BFO because BSE index futures live on BFO.

## Market sessions: when persistence is valid

`app/ops/sessions.py` owns *when* data is expected; writers own *what* is persisted and
never carry exchange timings. A `MarketSession` (equity derivatives, equity cash) has an
open/close, an optional pre-open window, a `capture_pre_open` policy, and an `enabled`
flag; a `SessionRegistry` maps each **artifact** (each index file, `STOCKS`, and later the
consolidated index-F&O dataset) onto a session. Several artifacts sharing a session share
one piece of configuration — there is deliberately no `NIFTY_CLOSE` / `STOCK_FNO_CLOSE`.

Three questions fall out of it, deliberately of differing strictness:

| question | method | used for |
|---|---|---|
| which lifecycle phase? | `phase()` → `INACTIVE`/`BOOTSTRAP`/`PRE_OPEN`/`OPEN`/`CLOSED` | display, telemetry |
| is a frame *owed*? | `is_capture_expected()` | writing frames, loss denominator |
| is absent data a *fault*? | `is_stale_armed()` | restart-first escalation |

`is_stale_armed` is strictly narrower than `is_capture_expected`: the pre-open auction is
legitimately silent, and the first `CAPTURE_RECOVERY_ARM_DELAY_SECONDS` after the open are
a grace period. Unscheduled seconds are neither written nor counted as loss, and an open
stale spell is *cleared* when the session ends — without which a 15-minute pre-open spell
would breach the 60 s deadline the instant recovery armed and restart the process every
single trading day.

Every session time falls back to the legacy `MARKET_OPEN`/`MARKET_CLOSE` pair when unset,
so an existing deployment keeps its exact schedule until the session block is configured.

## Feed health: three signals, one classification

`app/capture/feed_health.py`. A single freshness boolean used to collapse three different
conditions into one alarm:

* **transport** — are broker packets arriving at all? (`transport_age_ms`)
* **artifact** — is a particular dataset receiving relevant updates? (`artifact_ages_ms`,
  tracked per artifact in `apply_ticks`)
* **content** — are the values actually changing? (the existing rolling digest)

Classified worst-first into `RECOVERY_ABANDONED`, `RECOVERY_PENDING`, `TRANSPORT_STALE`,
`ARTIFACT_STALE`, `QUIET`, `HEALTHY`, `INACTIVE`. The distinction is not cosmetic — it
decides whether the process restarts:

* **TRANSPORT_STALE** (or every artifact stale) → the restart spell accumulates and
  escalation applies. This is the 2026-08-06 shape.
* **ARTIFACT_STALE** while packets keep arriving → logged and exposed, **no restart**. One
  frozen dataset must not take down capture for every dataset that is working (§16).
* **QUIET** → not a fault at all. The old design could not express this and would have
  counted a quiet market towards a restart.

Market phase and feed health are separate dimensions and are never overloaded onto one
status variable: `PRE_OPEN` + `HEALTHY` and `OPEN` + `TRANSPORT_STALE` are both meaningful.

## Data-loss accounting: the schedule is the denominator

The expected grid comes from the session schedule and the trading date **alone**, never
from process uptime — `MarketSession.scheduled_seconds_between()` answers "how many market
seconds were owed between these two instants" with nothing running. That is what makes an
outage visible in its own loss figure: if capture is down 09:15→09:27, a
process-observed denominator starts at 09:27 and those 12 minutes silently vanish.

Telemetry reports both levels (§17.11):

* **total** — `scheduled_seconds_elapsed` vs `captured_seconds` → `missing_seconds`
* **breakdown** — `stale_feed_seconds`, `write_path_seconds`, `downtime_seconds`,
  `unclassified_seconds`, which reconcile with the total. Anything unattributable stays
  visible as unclassified rather than being forced into a category.

`app/ops/completeness.py` provides the after-the-fact audit path: given the scheduled
windows and the frame timestamps from `scan_frames(..., collect_timestamps=True)`, it
reconciles the two and returns the gaps. This deliberately does not consult telemetry —
a crash, `docker kill`, reboot or power cut destroys in-memory counters and can stop the
final snapshot from ever being written, which is exactly when completeness matters most.
The archive is the evidence; telemetry only attributes the cause. Sequence numbers cannot
serve this purpose because they reset to 0 whenever the process restarts.

The invariant: every scheduled second is either a persisted frame or accounted-for loss.
Unscheduled and explicitly-disabled time are not scheduled seconds and take no part.

## Resilience

Recovery is **restart-first**: when the live feed dies mid-session the process exits and
Docker's `restart: unless-stopped` brings up a clean one. A restart re-bootstraps the
socket, the subscriptions and the token in a single step, and it is cheap because the
`.bin` files append — `resume_from_disk` restores the day's counters from the files
themselves plus the last persisted telemetry snapshot.

- **Stale detection** (`FreshnessMonitor`, `CAPTURE_STALE_SECONDS=5`): content-level, so
  it catches both a frozen feed (values repeating) and a total tick outage. While stale,
  frames are **not** written — see the stale-write suppression rules above.
- **Stale spell**: one continuous run of staleness. It only ends after
  `CAPTURE_STALE_RECOVERY_CONFIRM_SECONDS` (15 s) of *sustained* fresh ticks, so a flicker
  cannot reset it.
- **Escalation**: a spell longer than `CAPTURE_STALE_EXIT_SECONDS` (60 s), *while the
  market is trading*, raises `CaptureStalledError` → `CaptureController` → SIGTERM →
  container restart. A best-effort token swap is attempted first so the replacement
  process can start from a fresh token. The engine's `finally` has already drained and
  fsynced the writer queues by then, so no captured frame is lost.
- **Arming**: escalation is disarmed outside `PHASE_OPEN` and for
  `CAPTURE_RECOVERY_ARM_DELAY_SECONDS` (300 s) after `MARKET_OPEN`. Capture starts at
  `MARKET_OPEN` (09:10 as deployed) but NSE's continuous session begins at 09:15, and
  silence before then is normal — an ungated deadline would exit the process every minute
  of every pre-open.
- **Restart budget**: `CAPTURE_STALE_EXIT_MAX_RESTARTS` (3) escalations per trading date,
  counted in a per-date ledger under `stats_dir` so the count survives the exits it
  records. Once spent, the process stays up and reports `recovery_abandoned` on `/health`
  (503) and in the dashboard banner instead of thrashing the container.
- **Market-hours aware**: capture during market hours; roll files + compress at close.

### What this replaced, and why

Until 2026-08-07 recovery was an in-process tiered ladder: cheap reconnects reusing the
token, then `reconnect_with_refresh` fetching a new token from calspread, with an
exponential backoff (base 5 s, cap 300 s, ~20 attempts per cycle, 3 cycles). The
deployment's own artifacts showed it failing in three independent ways:

- `refresh_broker_session` **invalidated the persisted token before fetching** a
  replacement. calspread answered `authenticated: false` every time — `token_refreshes`
  was `0` in every recorded session — so each attempt destroyed that day's session file
  for no benefit. `_state/` held 27 `session-2026-08-06.invalidated-*.json` files, spaced
  exactly on the backoff curve (5, 10, 20, 40, 80, 160, 300, 300…).
- **A flicker disarmed escalation.** One fresh second at 10:25:32 called
  `reconnect_policy.reset()`, so `reconnect_cycles` stayed `0`, `exhausted` stayed
  `False`, and the restart that would have fixed the feed never happened. The session lost
  5,445 seconds (91 minutes, 23.4% of the day).
- **It fired before the exchange opened.** Staleness was declared 5 s after capture
  started at 09:10, so the destructive token dance ran at every single open; it was
  harmless only on days when ticks happened to arrive immediately.

Sessions 2026-08-04/05/06 lost 542 s, 4,299 s and 5,445 s of market data this way, with
`session_loss_pct` of 0.31% proving the write path itself was healthy throughout.

### Reconnect drill

`TickerBridge.reconnect()` is retained for this drill and for tests; the live path no
longer calls it. Run it when changing anything in this area:

1. `ssh beonedge` and confirm capture is running mid-session.
2. Force a stale spell — block the ticker's egress or revoke the token upstream.
3. Watch `docker logs -f market-data-dwndr-backend` for:
   `feed stale (...)`, then `live feed stale for 60s while the market is trading`,
   then the process exiting and a replacement logging
   `ticker connected; subscribing N tokens (full mode)`.
4. Confirm `/api/stats` shows the day's `escalations` incrementing and
   `grid_seconds_lost` growing only by the restart downtime.
5. Confirm the dashboard banner showed "Feed stale for Ns" while it was happening.

**Open question, deliberately unresolved:** whether an in-process `reconnect()` can
re-establish a socket at all with kiteconnect 5.x, whose `connect(threaded=True)` runs a
process-global twisted reactor on a new thread. Restart-first sidesteps it. If a drill
ever shows in-process reconnect working reliably, it could be reintroduced as a cheap
first step *before* the deadline — but never as the destructive token refresh it was.
