---
title: TickVault Data-Loss Root Cause and Remediation Plan
area: operations
type: investigation
status: implemented
tags: [area/operations, type/investigation, status/implemented, data-loss, forensics]
up: "[[Home]]"
related: ["[[distributed-capture-architecture-plan]]", "[[as-built-repository-overview]]", "[[live-data-pipeline]]", "[[broker-session-authentication]]"]
investigated: 2026-08-30
implemented: 2026-08-30
deployed_release: v0.1.46 (git 61efd8d, built 2026-08-11T08:12:02Z)
---

> [!success] Implementation status — 2026-08-30
> **P0-1 … P0-4, P1-1, P1-2, P1-4, P2-1, P2-2 and P2-3 are implemented in the working tree
> and not yet deployed.** Verification: backend **619 tests pass** (was 573; +46), ruff
> clean, no new mypy errors (7 pre-existing ones fixed); frontend typecheck, eslint,
> 148 vitest tests and `next build` all clean.
>
> Both root causes were re-verified against the evidence that found them:
> * **RC-1** — the offline loopback experiment from §10.1 now shows the *second* ticker in a
>   process firing callbacks where it previously fired none.
> * **RC-2** — replaying the real 2026-08-27 generation sequence (97/5, 96/8692, 94/3856,
>   95/10102) through the writer and re-scanning with the same standalone parser reports
>   **0 files with frames on a foreign axis**, against 8 of 15 NIFTY sessions before.
> * The new archive reconciliation independently reproduces the 2026-08-28 row —
>   `captured_seconds 19,708`, `missing 3,392`, **14.684%** — the session the dashboard
>   reported as 0.77%.
>
> Still outstanding, deliberately: the two **operator `.env` changes** in §24/P2-3
> (`MARKET_HOLIDAYS`, `CAPTURE_START_TIME=09:10`) and the **retroactive sidecar axis map**
> for the eight already-corrupted NIFTY sessions (§27.12). Neither is a code change.
> Note `CAPTURE_START_TIME` cannot be set to `BOOTSTRAP_TIME`'s 08:55, because
> `_validate_daily_schedule` requires `AUTH_POLL_END (09:00) <= capture_start`.
>
> A per-fix map of what changed is in §30.

# TickVault Data-Loss Root Cause and Remediation Plan

Forensic investigation of apparent market-data loss in `market_data_dwndr` / TickVault,
conducted 2026-08-30 against the deployed system on `beonedge-vps`
(`/srv/dev_stack/DATA_DOWNLOADER`, release **v0.1.46**).

Evidence base: source-code trace of the running release; the deployed `.env`; 46 escalation
ledger entries; 26 persisted monitor snapshots; 24 session-history records; 39 compression
sweeps; `docker logs` from 2026-08-17 15:45 IST onward; an **independent frame-level scan of
17 production `.bin.zst` archives** using a purpose-written parser validated against the
project's own writer; a headed-browser capture of the deployed `/monitor` dashboard; and one
controlled offline experiment reproducing the primary defect.

No fixes were implemented. No production state was modified.

---

## 1. Executive summary

**The reported percentages are two different metrics, and the operator has been comparing
them to each other.** "~15% on the previous session" is `scheduled_loss_pct` /
`frame_loss_pct` for 2026-08-28 (dashboard: *"Frame loss 14.76%"*, *"Missing 3,391s
(14.69%)"*). "3–5% on multiple sessions" is the Session-history **"Data loss"** column,
which is `data_loss_pct` — a different, process-relative denominator. On 2026-08-28 that same
column reads **0.77%**, and the dashboard simultaneously displays a green **"feed healthy"**.
Both numbers are computed correctly by their own definitions; only one of them is honest
about downtime.

**The loss is genuine, and it is now fully attributed with zero unexplained residual.**
Across the nine sessions since the 2026-08-18 configuration change (207,900 owed
artifact-seconds), 6,246 seconds are missing — **3.005%** — decomposing as:

| Cause | Seconds | Share of loss | Share of owed |
|---|---:|---:|---:|
| Calspread served a Kite-rejected token (2026-08-28 late start) | 3,239 | 51.9% | 1.558% |
| **Ticker never connects on the 2nd capture session in a process** | 1,817 | 29.1% | 0.874% |
| Mid-day stale → escalate → restart cycles (22 restarts) | 1,135 | 18.2% | 0.546% |
| Bootstrap latency at the open (head loss, 5–8 s/session) | 55 | 0.9% | 0.026% |
| **Unexplained** | **0** | **0.0%** | **0.000%** |

**Three confirmed root causes**, in order of how much they should worry you:

1. **P0 — A dead first feed, every day, caused by a Twisted-reactor singleton.**
   `kiteconnect.KiteTicker.connect(threaded=True)` starts the process-global Twisted reactor
   in a daemon thread. `ticker.close()` does **not** stop it. The *second* `KiteTicker` in the
   same process therefore calls `connectWS` cross-thread onto an already-running reactor and
   **is never serviced** — no exception, no callback, no ticks. Production correlation is
   exact across 38 capture starts: 1st-in-process connects **31/31**, 2nd-in-process connects
   **0/7**. Reproduced offline. Each occurrence writes **exactly 5 zero-filled frames**, then
   leaves a ~295 s hole until the escalation ladder restarts the process.

2. **P0 — Silent strike-axis corruption in the archived index option chains.**
   `IndexBinWriter.write_header` assigns the frame width *before* the header-once guard, so
   after a mid-day restart the on-disk header keeps the **old** strike vector while frames are
   written at the **new** width. Because `option_chain_filter` re-centres the ATM window,
   every column is mis-attributed by the window shift. **8 of 15 NIFTY sessions are affected,
   ~128,110 frames**; on 2026-08-26 and 2026-08-27 that is 99.98% of the day. No counter,
   test, or dashboard field detects it. This is worse than the missing seconds: the data that
   *was* captured is silently wrong.

3. **P0 (upstream) — The ~15% session was an authentication outage, not a capture fault.**
   On 2026-08-28 Calspread returned HTTP 200 with an access token every 60 s from 08:30:02
   IST, and Kite rejected it with **403 Forbidden** every single time, until 10:08:49. Capture
   physically could not start. Once it did, it was 99.2% clean. TickVault logged a `WARNING`
   every 60 s and raised no alarm, while the headline metric read 0.77%.

**What is *not* wrong.** The 1 Hz scheduler is wall-clock aligned and drift-free (0 off-grid
timestamps in 17 archives). The binary codec is immaculate (0 torn frames, 0 duplicate
timestamps, 0 non-monotonic sequences across ~5.5 GB/day files). Reconnect resubscribes the
full token universe. `engine.captures` equalled frames-on-disk in **14 of 14** sessions
checked against the archive. Queue overflow, tick-routing loss, writer backpressure, EOD
integrity and VPS resource pressure are all ruled out with evidence.

**Highest-leverage fix:** eliminating cause 1 removes ~29% of all loss and, more importantly,
removes the restart churn that *creates* cause 2. Fixing 1 and 2 together takes genuine
unexplained loss to zero and makes the archive trustworthy.

---

## 2. Incident description

Reported: ~3–5% apparent data loss on multiple sessions, ~15% on the previous trading
session. Investigated on Sunday 2026-08-30 with no active session, using historical
telemetry, archives and logs.

Deployed runtime at time of investigation:

| Property | Value | Source |
|---|---|---|
| Release | `v0.1.46`, git `61efd8d`, built 2026-08-11T08:12:02Z, `git_dirty: true` | `manifest.json`, `version.json` |
| Backend container | `market-data-dwndr-backend:v0.1.46`, created 2026-08-17T10:15:04Z | `docker inspect` |
| Last start | 2026-08-28T09:20:53Z = **14:50:53 IST**, `ExitCode=0`, `OOMKilled=false` | `docker inspect` |
| Docker `RestartCount` | **19** (frontend: **0**) | `docker inspect` |
| Process starts in logs | **31** since 2026-08-17 15:45 IST | `docker logs` |
| Escalations recorded | **46** across 2026-08-11 … 2026-08-28 | `escalations-*.json` |
| Deploy directory | bundle only — `.env`, `docker-compose.yml`, `images/`, `manifest.json`, `MARKET_DATA/`, `deploy.sh`, `rollback.sh`. **Not** a source checkout | `ls` |

The last trading session before the investigation was **Friday 2026-08-28**, which is the
"~15%" session.

---

## 3. Meaning of the current "data loss" metric

This was the highest-priority question, and it must be settled before any percentage is
treated as authoritative.

### 3.1 There is no single metric — there are four, from one kernel

All runtime percentages pass through `backend/app/capture/monitor.py:74-80`:

```python
def frame_loss_pct(frames_written: int, frames_expected: int) -> float:
    """Percent of the full-session baseline not yet captured (0..100, clamped)."""
    if frames_expected <= 0:
        return 0.0
    loss = (frames_expected - frames_written) / frames_expected * 100.0
    return max(0.0, min(100.0, loss))
```

Note the clamp: an over-count reads as a perfect **0.0%**, so a broken denominator is
invisible rather than alarming.

| Metric | Numerator | Denominator | Honest about downtime? |
|---|---|---|---|
| `frame_loss_pct` | `engine.captures` (global) / `writer.frames_written` (per artifact) | `expected_frames` = whole-day scheduled seconds (**23,100**) | Yes (fixed baseline) |
| `scheduled_loss_pct` | `engine.captures` | `SessionRegistry.scheduled_seconds_elapsed` | **Yes** |
| `data_loss_pct` | `engine.captures` | `grid_seconds_elapsed = (last_grid_ms − first_grid_ms)//1000 + 1` | **No** |
| `session_loss_pct` | `engine.captures` | `grid_seconds_elapsed − stale_seconds` | No |

`grid_seconds_elapsed` is bounded by the first and last grid second **the process itself
observed** (`monitor.py:82-98`, `engine.py:486-495`). It therefore **shrinks to fit the
outage**: a process that starts 54 minutes late has a denominator 54 minutes shorter, and the
missing time vanishes from the metric.

### 3.2 The two reported figures, resolved

Captured live from `http://tickvault.beonedge.internal/monitor`:

- Headline KPI: **`Frame loss 14.76%`** — `1,57,528 / 1,84,800 full-session frames`
- Data-loss diagnostics: `Scheduled 23,082s · Captured 19,691s · **Missing 3,391s (14.69%)**
  · feed stale 67s · downtime 3,177s · write fail 147s`
- Same panel, same session: **`Data loss 0.771% (incl. stale)`**, **`Elapsed loss 0.435%
  (gaps only)`**, and a green **`feed healthy`**
- Session-history **"Data loss"** column: `2026-08-28 → 0.77%`, `08-27 → 1.90%`,
  `08-26 → 1.28%`, `08-25 → 2.30%`, `08-13 → 3.01%`, `08-11 → 6.22%`

So:

> **"~15% on the previous session"** = `frame_loss_pct` / `scheduled_loss_pct` for
> 2026-08-28. **Genuine.** 14.69% of that day's owed seconds have no frame.
>
> **"3–5% on multiple sessions"** = the Session-history `data_loss_pct` column. **Also
> genuine, but a different denominator** — and it structurally *cannot* see the kind of
> outage that produced the 15%.

For 2026-08-28 the same session is simultaneously reported as **14.76%** and **0.77%** — a
19× spread, both labelled as loss, on one screen.

### 3.3 The archive-truth reconciliation is dead code

`backend/app/ops/completeness.py` computes `data_loss_pct` from **distinct second-slots
actually present in the file** intersected with scheduled windows — the only definition in
the repo that cannot be fooled by a counter. Its docstring says loss "is deliberately **not**
derived from runtime counters."

`reconcile()` and `classify_downtime()` have **no caller anywhere in `backend/app`** — only
`backend/tests/`. Every number the dashboard shows comes from in-memory counters on
`CaptureEngine`. The module that would have caught the 2026-08-28 outage honestly is present,
tested, and not wired up.

### 3.4 Verification: the counters are trustworthy where they are defined

I compared `engine.captures` (session-history) against frames independently counted from the
archive for every session with an archive scan:

| Session | `captures` | Archive frames (NIFTY) | Match |
|---|---:|---:|:--:|
| 08-11 | 20,814 | 20,814 | ✅ |
| 08-12 | 22,425 | 22,425 | ✅ |
| 08-13 | 21,818 | 21,818 | ✅ |
| 08-14 | 22,055 | 22,055 | ✅ |
| 08-17 | 22,292 | 22,292 | ✅ |
| 08-18 | 22,847 | 22,847 | ✅ |
| 08-19 | 22,704 | 22,704 | ✅ |
| 08-20 | 22,680 | 22,680 | ✅ |
| 08-21 | 22,951 | 22,951 | ✅ |
| 08-24 | 22,750 | 22,750 | ✅ |
| 08-25 | 22,560 | 22,560 | ✅ |
| 08-26 | 22,799 | 22,799 | ✅ |
| 08-27 | 22,655 | 22,655 | ✅ |
| 08-28 | 19,708 | 19,708 | ✅ |

**14/14 exact.** The theoretical enqueue-vs-durable-write divergence did not materialise. The
metric's problem is its *denominator and label*, not its numerator.

And `missing_seconds` reconciles exactly with the archive:
`archive missing_within_span + (first_frame − session_open) = reported missing`, **14/14**
(e.g. 08-27: 438 + 7 = 445; 08-28: 153 + 3,239 = 3,392).

---

## 4. Deployed architecture

`/srv/dev_stack/DATA_DOWNLOADER` is a self-contained release bundle, **not** a git checkout:
`.env` (0600), `docker-compose.yml`, `images/`, `manifest.json`, `version.json`, `GUIDE/`,
`deploy.sh`, `rollback.sh`, and the live `MARKET_DATA/` root (uid 10001).

Two hardened containers (read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, uid 10001,
tmpfs `/tmp`, healthchecks). Backend `HTTP_PORT=9000`, frontend `PORT=3789`, both published on
`HOST_BIND_ADDRESS=127.0.0.1`; reachable as `tickvault.beonedge.internal` (Tailscale
100.122.85.101) via an nginx vhost. No CPU or memory limit is set on either container.

Deployed configuration that matters (secrets omitted):

```ini
MARKET_DATA_PATH=/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA
ARCHIVE_DATA_PATH=/srv/data/DATA_DOWNLOADER/ARCHIVE
STATS_DATA_PATH=/srv/dev_stack/DATA_DOWNLOADER/MARKET_DATA/_state/stats

INDICES=NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX,BANKEX   # 6 indices
STOCK_UNIVERSE=all                                          # 210 F&O stocks discovered
CAPTURE_HZ=1
MARKET_HOLIDAYS=                                            # EMPTY  <-- latent defect

MARKET_TIMEZONE=Asia/Kolkata
BOOTSTRAP_TIME=08:55                                        # no consumer in app/
CAPTURE_START_TIME=09:15                                    # process window == session
CAPTURE_END_TIME=15:40
MARKET_OPEN=09:15
MARKET_CLOSE=15:40
EQUITY_DERIV_PREOPEN_START=09:00
EQUITY_DERIV_PREOPEN_END=09:15
EQUITY_DERIV_OPEN=09:15
EQUITY_DERIV_CLOSE=15:40
EQUITY_CASH_OPEN=09:15
EQUITY_CASH_CLOSE=15:40

AUTH_POLL_START=08:30
AUTH_POLL_END=09:00
AUTH_POLL_INTERVAL_SECONDS=60
CAPTURE_STALE_SECONDS=5
CAPTURE_STALE_EXIT_SECONDS=40                               # tightened from 60 on 08-17
ZSTD_LEVEL=17
ZSTD_THREADS=6
KITE_RATE_BROKER_URL=https://calspread.online/api/rf
```

Confirmed at runtime by the bootstrap log line, every session:

```
app.capture.bootstrap: session schedule: equity_deriv 09:15-15:40
                       (pre-open not captured, 23100 scheduled seconds/day)
```

8 artifacts × 23,100 s = **184,800 frames/day** expected. 2,024–2,067 tokens subscribed on
one connection. `/monitor` is served by the **Next.js app** (not `app/static/monitor.html`),
polling `/api/auth/status`, `/api/stats`, `/api/capture/history`; zero console errors.

---

## 5. Live capture data flow

```
  Calspread /api/kite/token ──▶ SessionService._validate_broker_token
        │                            └─▶ Kite GET /user/profile   (403 = AUTH BLOCKED)
        ▼
  DailyAutomationService (5 s tick)  ops/automation.py:decide_automation
        │  08:30-09:00 FETCH_BROKER_TOKEN · 09:15-15:40 START_CAPTURE
        │  >=15:40 STOP_CAPTURE then RUN_EOD
        ▼
  CaptureController.start()  api/capture.py
        ▼
  bootstrap_capture()  capture/bootstrap.py
        ├─ InstrumentStore.get(NFO/NSE/BFO)  ──▶ _instruments/<date>/*.csv
        ├─ build_option_chain × 6            ──▶ IndexTable   (L1, ATM±50)
        ├─ discover_fno_board                ──▶ StockMatrix   (L5, 210 × spot+3 fut)
        ├─ discover_index_fno_board          ──▶ IndexFnoMatrix (6 × spot+3 fut)
        ├─ build_*_writer × 8                ──▶ FileWriterThread (unbounded queue, fsync)
        ├─ build_session_registry            ──▶ scheduled_seconds = 23,100
        ├─ load_capture_snapshot(carried)    ──▶ engine.resume_from_disk()
        └─ plan_subscriptions                ──▶ 2,024-2,067 tokens (limit 3000, safe 2700)
        ▼
  run_capture()  bridge.bind_loop() ─▶ bridge.start() ─▶ KiteTicker.connect(threaded=True)
        │                                                   └── ✗ DEFECT: 2nd in process
        ▼                                                       is never serviced
  TickerBridge._on_ticks ─(call_soon_threadsafe)─▶ asyncio.Queue(maxsize=10_000)
        ▼
  CaptureEngine._consume ─▶ apply_ticks() ─▶ stall.mark_message() ─▶ freshness.observe()
        ▼
  CaptureEngine.run()  wall-clock-aligned 1 Hz grid
        ├─ is_capture_expected(ts)?   (session registry)
        ├─ freshness.is_stale(ts)?    (global batch digest, 5 s)
        ├─ suppress ──▶ stale_seconds++ / unscheduled_seconds++   (NO frame written)
        └─ else ──▶ table.snapshot(ts) × 8 ──▶ writer.enqueue() ──▶ captures++
        ▼
  FileWriterThread.run() ─▶ append_frame ─▶ write + flush + os.fsync  ──▶ <date>.bin
        ▼
  EOD: stop ─▶ drain/fsync ─▶ zstd L17 ─▶ verify byte-for-byte ─▶ publish ─▶ unlink raw
```

Observability side-channel: `Broadcaster` builds Greeks off-thread, publishes to the WS hub,
and persists `capture-<date>.json` on a **60 s throttle**.

---

## 6. Session scheduling / expected-second analysis

**The session configuration is correct, and this is not a source of phantom loss.** This
contradicts `plans/distributed-capture-architecture-plan.md` §F1, which describes an earlier
deployed state.

NSE restructured the close effective **2026-08-03**: index and stock **derivatives** now trade
**09:15–15:40**; F&O-eligible **cash** and index **spot** end continuous trading at 15:15 with
a closing auction to 15:35. The deployed `.env` sets `EQUITY_DERIV_OPEN=09:15` /
`EQUITY_DERIV_CLOSE=15:40` and models the pre-open as a separate 09:00–09:15 window with
`EQUITY_DERIV_CAPTURE_PREOPEN` unset (→ `False`).

Consequences, verified in the persisted snapshots:

- `scheduled_seconds = 23,100` per artifact per day (09:15→15:40).
- `unscheduled_seconds = 0` on 2026-08-27 and 2026-08-28 — the pre-open is correctly excluded
  and there is no post-close tail being counted.
- The registry-derived baseline is authoritative (`bootstrap.py:214-221`), and
  `scheduled_seconds_elapsed` tracks the **session**, not process uptime
  (`sessions.py:194-198`), so downtime is genuinely counted as owed.

The 2026-08-17 → 2026-08-18 transition is visible in the data: `scheduled_seconds` moves
**22,500 → 23,100**, confirming the operator applied the 15:30 → 15:40 correction that
evening (simultaneously tightening `CAPTURE_STALE_EXIT_SECONDS` 60 → 40, which is why
mid-day restart gaps shrink from 66–85 s to 43–57 s at the same boundary).

### 6.1 Residual scheduling defects (real, but not the current loss)

1. **`CAPTURE_START_TIME` equals `EQUITY_DERIV_OPEN` (both 09:15).** The capture *window* was
   designed to open *before* the session so the socket is live before the first print
   (`config.py:556-570`). With them equal, bootstrap latency is charged as loss every day —
   measured at **5–8 s/session** (55 s across 9 sessions). `BOOTSTRAP_TIME=08:55` is set but
   **has no consumer anywhere in `app/`**.

2. **The recovery arm delay is measured from `open_at`, and it is the multiplier on cause 1.**
   `is_stale_armed_at` (`sessions.py:124-139`) arms at `open_at + 300 s` = **09:20:00**. A dead
   feed at 09:15 therefore cannot escalate for 295 s. **8 of 14 escalation ledgers contain
   `09:20:00` to the second** — the escalation fires at the first instant it is permitted:

   ```
   2026-08-11  09:20:00, 10:16:32, 10:31:33, 14:01:36
   2026-08-13  09:20:00, 10:16:58, 12:31:58, 14:22:05, 15:02:06, 15:17:12
   2026-08-14  09:20:00, 11:32:25, 13:42:27
   2026-08-19  09:20:00, 09:26:39
   2026-08-20  09:20:01, 11:56:50, 14:41:51
   2026-08-24  09:20:00, 09:50:09
   2026-08-25  09:20:00, 10:40:14, 10:55:17, 12:25:16, 13:05:17, 14:35:19
   2026-08-26  09:20:00
   2026-08-27  09:20:00, 11:45:39, 12:50:42, 13:20:45
   ```

3. **`MARKET_HOLIDAYS` is empty.** `TradingCalendar.is_trading_day` only excludes weekends
   (`calendar.py:62-66`), so any weekday exchange holiday will be given a full 23,100 s
   denominator with zero captures and reported as **100% loss** — and `decide_automation`
   will emit `START_CAPTURE` and `RUN_EOD` on it. Latent; no holiday fell in the window
   examined.

4. **`EXPECTED_FRAMES_PER_SESSION` is unreachable.** `expected_frames = session_frames or
   settings.expected_frames_per_session` (`bootstrap.py:221`) short-circuits, so an operator
   setting it to "correct" the metric would see no effect. Conversely, if a session were
   *disabled* (`scheduled_seconds()==0`) the fallback would resurrect a 23,100 baseline
   against zero captures — reporting 100% loss for a deliberately disabled artifact.

5. **All 8 artifacts share one `equity_deriv` session** (`bootstrap.py:198-202`), including
   BSE `SENSEX`/`BANKEX` and the `STOCKS` file whose **spot legs stop trading at 15:15**.
   `SESSION_EQUITY_CASH` is constructed but never assigned. Today this costs nothing
   measurable — derivatives keep ticking 15:15–15:40 so the global freshness digest keeps
   changing and writes continue — but it means the 15:15–15:40 spot columns carry a
   legitimately frozen last-traded value that is indistinguishable from a frozen feed.

---

## 7. Historical session comparison

From `session-history.jsonl` (24 sessions). `sched` = `scheduled_seconds`; `sl%` =
`scheduled_loss_pct`; `dl%` = `data_loss_pct` (the Session-history "Data loss" column);
`esc` = escalations.

| Session | sched | captures | miss | **sl%** | **dl%** | stale s | gaps | g_lost | esc | recon | drop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-08-10 | 22,500 | 19,295 | 3,205 | **14.24** | **0.00** | 0 | 0 | 0 | 0 | 0 | 0 |
| 2026-08-11 | 22,500 | 20,814 | 1,686 | 7.49 | 6.22 | 1,389 | 5 | 1,372 | 4 | 0 | 0 |
| 2026-08-12 | 22,500 | 22,425 | 75 | 0.33 | 0.30 | 56 | 1 | 65 | 1 | 0 | 0 |
| 2026-08-13 | 22,500 | 21,818 | 682 | 3.03 | 3.01 | 426 | 6 | 663 | 6 | 0 | 0 |
| 2026-08-14 | 22,500 | 22,055 | 445 | 1.98 | 1.96 | 314 | 3 | 434 | 3 | 0 | 0 |
| 2026-08-17 | 22,500 | 22,292 | 208 | 0.92 | 0.90 | 121 | 3 | 198 | 3 | 0 | 0 |
| 2026-08-18 | 23,100 | 22,847 | 253 | 1.10 | 1.06 | 70 | 5 | 237 | 5 | 0 | 0 |
| 2026-08-19 | 23,100 | 22,704 | 396 | 1.71 | 1.68 | 299 | 2 | 338 | 2 | 0 | 0 |
| 2026-08-20 | 23,100 | 22,680 | 420 | 1.82 | 1.79 | 284 | 3 | 409 | 3 | 0 | 0 |
| 2026-08-21 | 23,100 | 22,951 | 149 | 0.65 | 0.62 | 65 | 3 | 138 | 3 | 0 | 0 |
| 2026-08-24 | 23,100 | 22,750 | 350 | 1.52 | 1.49 | 237 | 2 | 339 | 2 | 0 | 0 |
| 2026-08-25 | 23,100 | 22,560 | 540 | 2.34 | 2.30 | 342 | 6 | 522 | 6 | 0 | 0 |
| 2026-08-26 | 23,100 | 22,799 | 301 | 1.30 | 1.28 | 238 | 1 | 294 | 1 | 0 | 0 |
| 2026-08-27 | 23,100 | 22,655 | 445 | 1.93 | 1.90 | 305 | 4 | 430 | 4 | 0 | 0 |
| **2026-08-28** | 23,100 | 19,708 | **3,392** | **14.68** | **0.77** | 67 | 3 | 147 | 3 | 0 | 0 |

Three comparison sessions, as required:

- **Healthy — 2026-08-21 (0.65%).** The first ticker connected at 09:15:06. No morning hole.
  All 149 missing seconds are 3 mid-day restart gaps (49/47/46 s) plus 7 s of head loss.
- **Typical — 2026-08-27 (1.93%).** First ticker never connected → one 295 s morning hole,
  then 3 mid-day restarts (49/47/47 s), plus 7 s head loss. 438 + 7 = 445.
- **Worst — 2026-08-28 (14.68%).** 3,239 s late start from the auth outage, then 3 restart
  gaps (47/49/57 s). 153 + 3,239 = 3,392.

Two diagnostic signatures across the whole table:

- **`reconnects = 0` in every single session.** `TickerBridge.reconnect()` and
  `ReconnectPolicy` are dead code; recovery is restart-only.
- **`dropped_batches = 0` and `unmatched_ticks = 0` in every session.** No ingest overflow,
  no token-routing loss.
- `2026-08-10` shows the same signature as `2026-08-28`: `dl% = 0.00` with `sl% = 14.24`
  (3,205 s unobserved). It was the 6-index/`INDICES_FnO` rollout day — the snapshot size
  doubles from 4.4 KB to 9.1 KB at that date.

`Ticks/s` in the UI ranges 28.7 → 927.3 and is meaningless: `ticks_received` is
process-local, is not carried across restarts, and `record_session_summary` **replaces** the
row for a trading date, so the surviving row is whichever generation happened to stop last.

---

## 8. ~15% loss session incident timeline — 2026-08-28

All times IST. Sources: `docker logs`, `escalations-2026-08-28.json`,
`capture-2026-08-28.json`, and my independent archive scan.

| Time | Event | Evidence |
|---|---|---|
| 08:30:02 | Auth poll opens. `GET calspread.online/api/kite/token` → **200 OK** | log |
| 08:30:02 | `GET api.kite.trade/user/profile` → **403 Forbidden** | log |
| 08:30:03 | `app.ops.automation: daily automation action failed: FETCH_BROKER_TOKEN (KiteLoginError)` | log |
| 08:31 … 10:07 | **Identical 200-then-403 cycle repeats every ~60 s for 98 minutes** | log |
| 09:15:00 | Session opens. Frames are now owed. Capture cannot start — no valid session | `sessions.py` |
| 10:08:49 | `GET /user/profile` → **200 OK**. Token finally accepted | log |
| 10:08:56-58 | Bootstrap: NIFTY 94 strikes (exp 2026-09-01), BANKNIFTY 101, FINNIFTY 91, MIDCPNIFTY 100, SENSEX 101, BANKEX 101; 210 F&O stocks; 6 indices × 3 futures | log |
| 10:08:58 | `capture started for 2026-08-28 (2040 tokens)` — **2nd capture in this process** | log |
| — | **No `ticker connected` line. The socket never establishes.** | log (absence) |
| 10:08:59–10:09:03 | **Exactly 5 zero-filled frames** written (freshness tolerates 5 s before the first tick) | archive: `widths {'94': …}`, first_ts 10:08:59 |
| 10:09:04 | Feed declared stale; writes suppressed | `engine.py:441-451` |
| 10:09:44 | **Escalation #1** → `CaptureStalledError` → `SIGTERM` | `escalations-2026-08-28.json` |
| 10:09:47 | Process restart. `resumed Kite session for 2026-08-28 (market phase: OPEN)` | log |
| 10:09:49 | `resumed mid-session: 5 frames already on disk … 45s downtime counted as lost` | log |
| 10:09:49 | `capture started (2040 tokens)` — **1st capture in a fresh process** | log |
| 10:09:50 | **`ticker connected; subscribing 2040 tokens (full mode)`** — 1 second later | log |
| 10:09:51 | Frames resume; `sequence` resets to 0 | archive: reset at 10:09:51 |
| 13:25:51 | Escalation #2 → restart → ticker connected 13:25:59 → **49 s gap** | ledger + archive |
| 14:50:52 | Escalation #3 → restart → ticker connected 14:51:08 → **57 s gap** | ledger + archive |
| 15:39:42 | Final `capture-2026-08-28.json` persisted (60 s throttle) | file mtime |
| 15:39:59 | Last frame | archive |
| 15:40–15:45 | EOD: 8 files, 7.77 GB → 0.22 GB, ratio 35.76×, verified, published | `compression-history.jsonl` |

**Composition of the 14.68%:** it is **one dominant outage plus three small restarts**, not
many small gaps and not systemic loss.

```
3,392 missing seconds = 3,239 late start (auth)  +  47 (dead 1st ticker)  +  106 (2 restarts)
                        95.5%                       1.4%                     3.1%
```

Once running, the process wrote 19,708 of the 19,861 grid seconds it observed — **99.23%**.
All 8 artifacts recorded identically (19,708 frames each), so no dataset was singled out.

**The reason this was not noticed for 98 minutes:** the failure surfaced only as a 60-second
`WARNING`. `/health` stayed 200 (capture was not "dead", it had not started). No alarm exists
for "the session is open, frames are owed, and capture is not running."

---

## 9. Gap distribution analysis

Derived independently from 17 archived `.bin.zst` files with a standalone parser
(`/tmp/tv_forensics/binscan.py`) validated against the project's own `IndexBinWriter` /
`StockBinWriter`.

### 9.1 Per-session topology (NIFTY; all artifacts are identical)

| Session | frames | first | last | span | miss | gaps | largest gaps (start, seconds) |
|---|---:|---|---|---:|---:|---:|---|
| 08-11 | 20,814 | 09:20:06 | 15:29:59 | 22,194 | 1,380 | 5 | (13:25:33, **1129**) (14:00:34, 76) (10:15:31, 66) (10:30:32, 66) |
| 08-12 | 22,425 | 09:15:08 | 15:29:59 | 22,492 | 67 | 1 | (10:05:41, 67) |
| 08-13 | 21,818 | 09:15:06 | 15:29:59 | 22,494 | 676 | 7 | (09:15:10, **296**) (14:21:04, 85) (15:16:11, 82) (15:01:05, 76) |
| 08-14 | 22,055 | 09:15:05 | 15:29:59 | 22,495 | 440 | 3 | (09:15:09, **297**) (13:41:25, 76) (11:31:24, 67) |
| 08-17 | 22,292 | 09:15:06 | 15:29:59 | 22,494 | 202 | 3 | (14:45:31, 69) (14:05:30, 67) (11:20:26, 66) |
| 08-18 | 22,847 | 09:15:08 | 15:39:59 | 23,092 | 245 | 5 | (15:20:52, 57) (13:00:50, 48) (10:35:47, 47) (10:45:48, 47) |
| 08-19 | 22,704 | 09:15:07 | 15:39:59 | 23,093 | 389 | 9 | (09:15:11, **294**) (09:25:58, 46) (15:08:41, 21) (12:02:48, 8) |
| 08-20 | 22,680 | 09:15:06 | 15:39:59 | 23,094 | 414 | 3 | (09:15:10, **296**) (14:41:09, 69) (11:56:07, 49) |
| 08-21 | 22,951 | 09:15:07 | 15:39:59 | 23,093 | 142 | 3 | (13:29:01, 49) (10:29:00, 47) (09:28:59, 46) |
| 08-24 | 22,750 | 09:15:07 | 15:39:59 | 23,093 | 343 | 2 | (09:15:11, **295**) (09:49:27, 48) |
| 08-25 | 22,560 | 09:15:08 | 15:39:59 | 23,092 | 532 | 6 | (09:15:12, **294**) (14:34:37, 50) (10:54:34, 48) (12:24:35, 47) |
| 08-26 | 22,799 | 09:15:05 | 15:39:59 | 23,095 | 296 | 1 | (09:15:09, **296**) — *the entire day's loss is one gap* |
| 08-27 | 22,655 | 09:15:07 | 15:39:59 | 23,093 | 438 | 4 | (09:15:11, **295**) (13:20:02, 49) (11:44:58, 47) (12:50:01, 47) |
| 08-28 | 19,708 | **10:08:59** | 15:39:59 | 19,861 | 153 | 3 | (14:50:11, 57) (13:25:10, 49) (10:09:03, 47) |

### 9.2 The distribution is bimodal and sharply quantised

Aggregated buckets over the sampled gaps:

```
   1 s : 3        31- 60 s : 26      <-- restart cycles
 2- 5 s : 1       61-300 s : 24      <-- restart cycles (old 60 s config) + morning holes
 6-30 s : 4          >300 s :  1      <-- the single 1,129 s outage on 08-11
```

Distinct gap lengths observed: `1, 2, 8, 21, 43, 46, 47, 48, 49, 50, 57, 66, 67, 68, 69, 76,
82, 85, 294, 295, 296, 297, 1129`. Median 49 s, mean 104 s.

Three clusters, each with an exact mechanical explanation:

| Cluster | Count | Mechanism |
|---|---:|---|
| **294–297 s** | 9 | Dead first ticker. Writes suppressed at `open+5 s`; escalation blocked until `open+300 s`. Gap = 300 − 5 − 1 + restart. Every one starts at 09:15:09–09:15:12. |
| **43–57 s** (from 08-18) | 26 | `CAPTURE_STALE_EXIT_SECONDS=40`. Gap = (40 − 5) + ~12 s restart ≈ 47 s. |
| **66–85 s** (to 08-17) | — | Same mechanism at the old `=60` setting: (60 − 5) + ~12 ≈ 67 s. |

This yields a measured **restart cost of ≈12 s**, not the 25–30 s asserted by
`test_restart_resume.py:120`.

**Only 8 of 59 sampled gaps are ≤30 s** (3×1 s, 1×2–5 s, 4×6–30 s). There is no population of
1-second jitter gaps — which independently rules out scheduler drift as a loss mechanism.

### 9.3 Structural integrity of every file scanned

| Property | Result across all 17 archives |
|---|---|
| `duplicate_timestamps` | **0** — the ≤60-frame catch-up duplication path never fired |
| `off_grid_timestamps` (ts % 1000 ≠ 0) | **0** — the grid is exactly wall-clock aligned |
| `monotonic` timestamps | **True** everywhere |
| `torn_prefix` / `torn_payload` | **0 / 0** — including 5.51 GB and 4.79 GB stocks files |
| `sequence` resets | 1–4 per file, each exactly at a restart (= generation marker) |

`STOCKS/2026-08-27.bin.zst` decoded to **5,511,719,092 bytes**, 22,655 frames, 210 rows,
header rows 210, single width `{210: 22655}` — clean.


---

## 10. WebSocket / subscription findings

### 10.1 The headline defect: the second ticker in a process is never serviced

**CONFIRMED.** This is the mechanism behind every 294–297 s morning hole.

`TickerBridge._start_ticker` (`app/kite/ticker.py:159-170`) creates a fresh `KiteTicker` and
calls `connect(threaded=True)`. Inside the SDK
(`kiteconnect/ticker.py:496-537`):

```python
def connect(self, threaded=False, disable_ssl_verification=False, proxy=None):
    self._create_connection(self.socket_url, ...)
    ...
    connectWS(self.factory, contextFactory=context_factory, timeout=self.connect_timeout)
    ...
    # Run when reactor is not running
    if not reactor.running:
        if threaded:
            opts["installSignalHandlers"] = False
            self.websocket_thread = threading.Thread(target=reactor.run, kwargs=opts)
            self.websocket_thread.daemon = True
            self.websocket_thread.start()
```

The Twisted reactor is a **process-global singleton**. `TickerBridge.stop()` calls
`ticker.close()`, and `close()` does **not** stop it:

```python
def close(self, code=None, reason=None):
    self.stop_retry()          # factory.stopTrying()
    self._close(code, reason)  # ws.sendClose()

def stop(self):
    reactor.stop()             # <-- never called by TickerBridge
```

(The only other `self.stop()` in the SDK, at `ticker.py:198`, is nested inside
`if self.debug:` and therefore unreachable in production.)

So on the second `connect()` in a process, `reactor.running` is already `True`, the thread is
not started, and `connectWS` is invoked **from the main thread onto a reactor running in
another thread** — an unsynchronised cross-thread reactor call that is silently never
serviced.

**Offline verification** (loopback only, `ws://127.0.0.1:9`, no Kite contact, nothing in
production touched):

```
reactor.running before any connect : False
reactor.running after T1.connect   : True
T1 callbacks fired                 : ['on_noreconnect', 'on_reconnect']
T1 reactor thread alive            : True
reactor.running after T1.close()   : True
reactor.running after T2.connect   : True
T2 has websocket_thread attr       : False
T2 callbacks fired                 : NONE
VERDICT: second ticker serviced?     NO
```

**Production correlation — 38 capture starts, zero exceptions:**

| `nth_capture_in_process` | ticker connected within 30 s | count |
|---|---|---:|
| 1 | **YES** | **31 / 31** |
| 2 | **NO** | **0 / 7** |

The seven failures are 08-19 09:15:06, 08-20 09:15:05, 08-24 09:15:06, 08-25 09:15:07,
08-26 09:15:04, 08-27 09:15:06, 08-28 10:08:58. In each case the *next* generation — a fresh
process — connected in **~1 second**:

```
08-27 09:15:06  CAPTURE_START     2046 tokens     <-- 2nd capture in this process
                (no ticker connected line at all)
08-27 09:20:05  CAPTURE_START     2044 tokens     <-- fresh process after SIGTERM
08-27 09:20:06  TICKER_CONNECTED  2044 tokens
```

A second capture session occurs in one process whenever the process survives a full trading
day — i.e. when the day's *last* generation is not itself killed by an escalation. It then
starts the next morning's capture as generation 2. The two exceptions in the window
(2026-08-18 and 2026-08-21) are exactly the days whose overnight process was fresh
(started 08-17 15:45 and 08-20 17:23 respectively, after EOD), so their 09:15 capture was
generation 1 and connected normally.

**Why nothing notices.** `_on_connect` never runs, so `self.connected` stays `False` — and
**nothing in the engine ever reads `bridge.connected`**; it is telemetry only. There is no
exception to catch, no `on_error`, no `on_reconnect`. The only detector is the freshness
monitor, 5 seconds later.

**Cost per occurrence:** 5 fabricated frames + a 294–297 s hole when the arm delay is still
in force, or ~47 s when it is not (2026-08-28 10:09:03, because 10:09 is past `open+300 s`).

### 10.2 Resubscription after reconnect is correct

**RULED OUT** as a loss cause. The token list is captured once at construction
(`ticker.py:73  self.tokens = list(tokens)`) and is never mutated anywhere in the codebase.
`_on_connect` replays the **entire** list on every connect:

```python
logger.info("ticker connected; subscribing %d tokens (full mode)", len(self.tokens))
ws.subscribe(self.tokens)
ws.set_mode(MODE_FULL, self.tokens)
```

Confirmed in production: every `ticker connected` line reports the full 2,024–2,067 tokens.
There is no incremental/delta subscription and no partial-state rebuild. `unmatched_ticks = 0`
in all 24 sessions confirms routing completeness.

### 10.3 Real but unexercised subscription weaknesses

- **No acknowledgement or coverage check.** `connected = True` is set purely on the absence of
  an exception. Kite's `subscribe` is a fire-and-forget WS frame; a broker-side rejection
  arrives asynchronously and never raises. `SubscriptionPlan.as_telemetry().subscribed_tokens`
  reports the **planned** count, never reconciled against what was accepted.
- **`subscribe()` and `set_mode()` share one `try/except`.** If `subscribe` succeeds and
  `set_mode` raises, the feed runs in reduced mode: ticks flow, freshness stays green, and
  `ticks.py:36-79` substitutes `0` for OI/OHLC and `EMPTY_LEVEL` for depth — writing
  structurally perfect, semantically fabricated frames. Not observed in this window.
- **Sharding is coded but unwired.** `plan_subscriptions` warns at `safe_limit = 2700` and
  `SubscriptionPlan.shards()` exists but has no caller; over-limit is log-only, never
  truncating. Current headroom is comfortable (2,067 of 2,700), so this is latent.
- **Nothing acts on `connected = False`.** No retry, no socket teardown, no health signal.

---

## 11. Feed freshness / stale-suppression findings

### 11.1 What "content-changing" means

`FreshnessMonitor` (`app/capture/reconnect.py:88-137`) hashes a 5-field digest over the whole
batch and compares it only with the immediately preceding batch:

```python
_DIGEST_FIELDS = ("last_price", "volume_traded", "oi",
                  "exchange_timestamp", "last_trade_time")
```

Properties that matter:

- **Batch-wide OR.** One field of one instrument changing marks the entire feed fresh.
- **Composition-sensitive.** `parts` is a positional tuple, so a change in batch *shape*
  registers as "content changed" even if no value moved. In production, where batch
  composition varies constantly, the content signal degrades toward a liveness check.
- **Before the first tick, age is measured from `_started_ms`** (`reconnect.py:139-146`). This
  is precisely why a dead socket still yields **exactly 5 frames**: seconds 1–5 are
  "not yet stale" at the 5,000 ms threshold, so five all-zero snapshots are written and
  persisted. Confirmed in the archive: `widths {'97': 5}` on 08-27, `{'98': 5}` on 08-26.

### 11.2 The write gate is global; the per-artifact signal is only logged

`capture_snapshot` (`engine.py:428-433`) consults **only** the global digest:

```python
scheduled = self.is_capture_expected(ts)
stale = self.freshness.is_stale(ts)
suppress = not scheduled or (stale and self.suppress_stale_writes)
```

`artifact_last_update_ms` / `stale_artifact_names` are never referenced in the write path.
`_report_stale_artifacts` (`engine.py:653-668`) logs at 3× tolerance (15 s) and explicitly
does not restart. Consequences:

- A **dead subset** is invisible. `artifact_last_update_ms[name]` is stamped if *any single*
  owned token ticks, so 209 of 210 dead stocks still read `HEALTHY`.
- **India VIX fans out to every index table** (`engine.py:186-193`), so a live VIX alone keeps
  all six index artifacts at age 0.
- Conversely, a genuinely **quiet** whole-universe moment suppresses writes on *all eight*
  artifacts at once. `feed_health.classify` distinguishes `QUIET` from a fault — but the write
  gate never calls it.

In this investigation window the aggregate signal behaved correctly: `feed_health` was
`HEALTHY` with `stale_artifacts: []` at both snapshots examined, and the 15:15–15:40 window
(where spot legs are structurally frozen) did not suppress writes, because derivatives keep
ticking and keep the digest moving.

### 11.3 Stale suppression is doing its job

`stale_seconds` is 65–342 on healthy sessions — i.e. **0.3–1.5%** of the day. Every observed
stale spell is either (a) the dead-ticker window, or (b) the 35 s pre-escalation window of a
restart cycle. There is no evidence of large-scale false staleness on a healthy feed.

Suppression itself is correct behaviour and should not be changed: writing frozen values into
a fixed-width frame would fabricate prints. The problem is not that holes exist; it is that
`stale_seconds` **under-counts** them (§16.3) and that the causes are conflated.

---

## 12. 1 Hz scheduler findings

**RULED OUT as a loss mechanism.** The loop is sleep-until-absolute-deadline, not
sleep-after-work (`engine.py:739-756`):

```python
interval_ms = max(1, int(round(interval_s * 1000)))
now0 = self._clock()
next_tick = ((now0 // interval_ms) + 1) * interval_ms      # snapped to a whole second
while not stop_event.is_set():
    sleep_s = (next_tick - self._clock()) / 1000.0          # remaining time, recomputed
    ...
```

`next_tick` only ever advances by fixed integer steps (`t += interval_ms`,
`engine.py:555`), so work cost is absorbed by a shorter sleep. **Drift over a 6h25m session:
0 ms. Drift-induced deficit: 0 frames.**

Empirically confirmed, which is stronger than reading the code: across all 17 archives,
`off_grid_timestamps = 0` — every one of ~350,000 frame timestamps is an exact multiple of
1,000 ms. If the grid were interval-based, timestamps would have walked off the second
boundary within minutes.

Two related paths that *could* have mattered and demonstrably did not:

- **Catch-up duplication.** `_due_ticks` emits up to `max_catchup=60` back-dated boundaries
  from one iteration, all built from the same live table state — fabricated duplicate content
  that also *passes* the freshness gate (because `is_stale(ts)` is evaluated against the
  back-dated `ts`). It never fired: `duplicate_timestamps = 0` in every file, and the largest
  intra-day gap population is 43–57 s, i.e. the loop was never merely "behind".
- **Snapshot build cost.** `snapshot_build_ms` reads **0.5 ms** on the live dashboard against
  a 1,000 ms budget — three orders of magnitude of headroom.

---

## 13. Restart / recovery findings

### 13.1 The escalation ladder as deployed

| Rung | Threshold | Source | Effect |
|---|---|---|---|
| Content stale | 5 s | `CAPTURE_STALE_SECONDS=5` | `stale_seconds++`, **writes suppressed** |
| Artifact-stale log | 15 s (3×) | `feed_health.py:58` | Log only; never restarts |
| Escalate | **40 s continuous, feed-wide, armed** | `CAPTURE_STALE_EXIT_SECONDS=40` | `CaptureStalledError` → `SIGTERM` → Docker restart |
| Recovery confirm | 15 s sustained fresh | `CAPTURE_STALE_RECOVERY_CONFIRM_SECONDS` | Ends a spell |
| Arm delay | **300 s past open → 09:20:00** | `CAPTURE_RECOVERY_ARM_DELAY_SECONDS` (default) | Gates everything above |
| Budget warning | 3 escalations/date | `CAPTURE_STALE_EXIT_MAX_RESTARTS=3` | **Warn only** — recovery never stops |

Observed in the logs, verbatim:

```
09:20:47 WARNING app.capture.engine: feed stale (content unchanged for 5950ms);
                 suppressing frame writes until fresh ticks resume (stale spell #1)
09:21:27 ERROR   app.api.capture: capture task failed (CaptureStalledError)
09:21:32 WARNING app.capture.bootstrap: this trading date has already escalated 1 time(s)
                 for a dead feed
09:21:32 INFO    app.capture.bootstrap: found today's persisted telemetry (8 streams) to
                 carry over: 0 gap(s), 0s lost, 27s stale
...
07:31:32 CRITICAL app.capture.engine: live feed stale for 40s and the nominal restart budget
                 (3) is already spent, but the market session is open — restarting anyway
                 rather than abandoning recovery
```

### 13.2 Quantified restart cost — measured, not assumed

Restart gap = `(stale_exit − 5) + restart_and_bootstrap`. Observed clusters give:

| Config | Expected | Observed gaps | Implied restart+bootstrap |
|---|---|---|---|
| `CAPTURE_STALE_EXIT_SECONDS=60` (to 08-17) | 55 + r | 66, 67, 68, 69, 76, 82, 85 | **≈12 s** |
| `CAPTURE_STALE_EXIT_SECONDS=40` (from 08-18) | 35 + r | 43, 46, 47, 48, 49, 50, 57 | **≈12 s** |

**The documented 25–30 s restart downtime is wrong for this deployment.** Actual cost is
≈12 s, and `test_restart_resume.py:120` (`assert 25 <= downtime_seconds <= 30`) pins a figure
that no longer reflects production.

Contribution across the nine post-08-18 sessions:

```
22 mid-day escalations x ~51.6 s mean = 1,135 s  =  18.2% of all missing seconds
 7 morning dead-ticker escalations             = 1,817 s  =  29.1%
```

Escalations are near-daily: **46 recorded across 14 sessions** (mean 3.3/day, max 6 on 08-13
and 08-25, min 1 on 08-12 and 08-26).

### 13.3 Restart accounting is sound but mislabelled

`resume_from_disk` (`engine.py:306-375`) is correct in aggregate — `captures` is re-seeded from
frames actually on disk, which is why the 14/14 archive match in §3.4 holds. Two defects:

- `downtime_s = max(0, (self._clock() - last_frame_ms) // 1000 - 1)` systematically
  **under-counts by one second per restart**.
- The result is added to `grid_seconds_lost`, which `_loss_breakdown` then reports as
  **"write fail"** — see §16.2. No write ever failed.

### 13.4 Recovery has only two rungs, and one is `SIGTERM`

`TickerBridge.reconnect()` — a cheap, correct, tested full teardown and resubscribe — **has no
caller in `app/`**. `ReconnectPolicy` (5 s → 300 s backoff, 20-attempt breaker) is imported and
re-exported but never instantiated. `StallDetector.is_stalled()` is never called.
`reconnects = 0` in all 24 sessions confirms it. Recovery is therefore: the SDK's opaque
internal retry, then killing the process.

`config.py:169-172` still tells operators that `CAPTURE_STALE_SECONDS` triggers "a self-driven
ticker reconnect". It does not.

### 13.5 Token refresh on escalation is safe

`SessionService.refresh_broker_session` (`session_service.py:112-159`) is fetch-first,
validate-second, replace-third; every failure path keeps the current session. This is the
fixed version of the destructive ladder that caused the 2026-07-29/31 damage — still visible
on disk as ~50 `session-2026-07-31.invalidated-*.json` files. `token_refreshes = 0`
throughout, and no `.invalidated-*` file exists after 2026-07-31. **Working as intended.**

---

## 14. Concurrency / backpressure findings

**RULED OUT.** Every queue was measured, and none was stressed.

| Buffer | Capacity | Overflow policy | Observed |
|---|---|---|---|
| `TickerBridge.queue` (asyncio) | 10,000 batches | drop-oldest, `dropped_batches++` | **`dropped_batches = 0` in all 24 sessions** |
| `FileWriterThread._queue` × 8 | **unbounded** | n/a (never blocks) | **`writer_lag_max = 0`** |
| `Broadcaster._latest_snapshot` | 1 (latest-wins) | coalesce | `snapshot_build_ms = 0.5` |

Writer latency is properly isolated: a thread per file, an unbounded queue so `enqueue` never
blocks, fsync on the writer thread, and `stop_writers` moved off the loop via
`asyncio.to_thread` (`engine.py:783-790`). Broadcaster Greeks work runs under
`asyncio.to_thread`.

Latent hazards that did not fire in this window, worth knowing:

- **A dead writer thread strands its queue silently.** `run()` stores the exception and stops
  consuming; queued frames are never drained and never counted. Detection is lazy — only on
  the next `enqueue`. `is_alive()` appears exactly once in the whole backend
  (`writer_thread.py:103`, inside `stop`).
- **`enqueue` across 8 writers has no try/except** (`engine.py:466-473`), so a raise on writer
  3 would leave a second in some files and not others, with `captures` un-incremented.
- **Unbounded writer queues convert a stalled disk into unbounded RSS growth**, not visible
  backpressure. Container RSS was 103.9 MiB, so nothing approached this.
- **Gate failures fail open**: `is_capture_expected` returns `True` on exception, inflating the
  denominator; `recovery_armed` returns `False`, disarming recovery.

---

## 15. Binary persistence findings

### 15.1 The codec is flawless; the header contract is not

Integrity across 17 archives including two multi-GB stocks files: **0 torn frames, 0 duplicate
timestamps, 0 off-grid timestamps, monotonic everywhere.** Every `sequence` reset lands exactly
on a restart. EOD verification passed on all 39 sweeps. The framing, writer, reader and
compressor are not losing anything.

### 15.2 CONFIRMED silent corruption: the strike axis

`IndexBinWriter.write_header` (`app/bin_codec/writer.py:205-212`):

```python
def write_header(self, header: IndexHeader) -> bool:
    """Write the header iff the file is empty. Returns True if written now."""
    self._n_strikes = int(header.strikes.shape[0])   # <-- width set BEFORE the guard
    if self._header_written:
        return False                                 # header on disk left untouched
    self._write_framed(encode_index_header(header))
    self._header_written = True
    return True

def append_frame(self, frame: IndexFrame) -> None:
    ...
    self._write_framed(encode_index_frame(frame, self._n_strikes))   # NEW width
```

After a mid-day restart the file keeps **generation 1's** `strikes` vector while every
subsequent frame is encoded at the **current** generation's width. And the window moves:
`option_chain_filter` (`app/chain/filter.py:71-75`) re-centres on ATM and clips at both ends —

```python
atm_idx = nearest_index(strike_keys, atm_paise)
start = max(0, atm_idx - strikes_per_side)
end   = min(len(strike_keys) - 1, atm_idx + strikes_per_side)
window = strike_keys[start : end + 1]
```

— so when spot rises and `end` is already clipped at the ceiling, `start` advances and the
window **shifts**. Column *j* of a later frame therefore belongs to a different strike than
`header.strikes[j]`. For NIFTY at a 50-point step, a 2-strike shift mis-attributes every
option by 100 index points.

**Controlled reproduction** (local, using the project's own writer): header written with 5
strikes, writer reopened with a 4-strike table → `write_header` returns `False`, and my
independent parser reads `header_axis_len = 5` with frame widths `{'5': 4, '4': 2}`.

**Production evidence** — header-declared axis versus actual frame column widths, NIFTY:

| Session | header | frame column widths | misaligned frames | share |
|---|---:|---|---:|---:|
| 2026-08-12 | 100 | `{98: 19391, 100: 3034}` | 19,391 | 86.5% |
| 2026-08-13 | 99 | `{98: 15274, 99: 6544}` | 15,274 | 70.0% |
| 2026-08-14 | 98 | `{97: 15612, 98: 6443}` | 15,612 | 70.8% |
| 2026-08-17 | 98 | `{96: 9838, 98: 12454}` | 9,838 | 44.1% |
| 2026-08-18 | 96 | `{94: 1090, 95: 16409, 96: 5348}` | 17,499 | 76.6% |
| **2026-08-26** | 98 | `{97: 22794, 98: 5}` | **22,794** | **99.98%** |
| **2026-08-27** | 97 | `{94: 3856, 95: 10102, 96: 8692, 97: 5}` | **22,650** | **99.98%** |
| 2026-08-28 | 94 | `{93: 5052, 94: 14656}` | 5,052 | 25.6% |

Clean: 08-11, 08-19, 08-20, 08-21, 08-24, 08-25 — all `{101: …}`, because NIFTY's ATM±50
window was not clipped on those days and the count could not change.

**8 of 15 NIFTY sessions, ~128,110 frames.** The widths map exactly onto the bootstrap log's
per-generation strike counts (08-27: `09:15:04→97, 09:20:04→96, 11:45:42→94, 12:50:45→95,
13:20:48→95` ⇒ `{97:5, 96:8692, 94:3856, 95:10102}`).

Why 08-26 and 08-27 are 99.98%: generation 1 wrote the header **and only 5 frames** before the
dead-ticker escalation, so the entire rest of the day is judged against a 5-frame generation's
strike vector.

Only NIFTY, FINNIFTY and MIDCPNIFTY are exposed, because only their strike lists are short
enough for the ±50 window to clip. BANKNIFTY, SENSEX, BANKEX, `STOCKS` (210 rows) and
`INDICES_FnO` (6 rows) showed a single stable width matching the header.

**Recoverable, but only offline.** The information needed is not in the `.bin`, yet it is
retrievable: `_instruments/<date>/NFO.csv` is archived for all 29 dates, each frame carries its
own `spot_price`, and generation boundaries are exactly the `sequence` resets. So the true
window per generation can be recomputed with `get_spot_atm` + `option_chain_filter`.

### 15.3 Other persistence defects found (not currently losing data)

- **`INDICES_FnO` is excluded from `resume_from_disk`** (`engine.py:322-325` builds `writers`
  from `index_writers` + `stock_writer` only). A day where only that file had frames would
  resume as `{"resumed": False}`.
- **`captures = max(frames_on_disk)`** across writers — the *best* artifact's count, so a
  lagging artifact's shortfall disappears from the global figure.
- **`scan_frames` miscounts a payload-truncated frame as good** (`scan.py:91-99` seeks past
  EOF, then `frames += 1`, and reports `truncated=False`), while `reader.py:132-140` correctly
  discards it. Never triggered here (0 torn frames).
- **`compress_file` can replace a verified archive** with a compression of a newer, shorter
  same-date `.bin` if capture ever restarts after EOD. The two zero-file sweeps on 08-17 and
  08-20 show second sweeps do happen; they found nothing, so no archive was harmed.
- `eod.prune_stale_raw` has no caller despite the docs claiming it runs at startup.

---

## 16. Monitoring / accounting findings

### 16.1 Decoding the exact panel the operator is reading

The Data-loss diagnostics panel for 2026-08-28 reads:

```
Scheduled 23,082s   Captured 19,691s   Missing 3,391s (14.69%)
feed stale 67s      downtime 3,177s    write fail 147s
```

These come from `_loss_breakdown` (`monitor.py:280-292`), which is a **residual cascade**, not
a measurement:

```python
missing    = max(0, scheduled_elapsed - captured)                    # 23082 - 19691 = 3391
stale      = min(missing, engine.stale_seconds)                      # min(3391, 67)  =   67
write_path = min(max(0, missing - stale), engine.grid_seconds_lost)  # min(3324, 147) =  147
downtime   = max(0, missing - stale - write_path)                    # 3391-67-147    = 3177
unclassified = ... # 0 by construction — it can never be non-zero here
```

Against ground truth from the archive and logs:

| Panel label | Panel | What actually happened |
|---|---:|---|
| `feed stale 67s` | 67 | Real suppressed seconds, but an **under-count** (§16.3). True suppressed ≈105–120 s. |
| `write fail 147s` | 147 | **No write ever failed.** This is `grid_seconds_lost`, i.e. the three restart holes (47 + 49 + 57 = 153 s) that `resume_from_disk` charged. |
| `downtime 3,177s` | 3,177 | The **3,239 s late start** caused by the Calspread 403 outage — understated by 62 s. |

True composition: `3,239 (late start) + 153 (three restart holes) = 3,392`.
Panel composition: `3,177 + 147 + 67 = 3,391`.

The totals agree; the **labels do not**. The 61 s discrepancy is exactly the double-counted
overlap between `stale_seconds` and `grid_seconds_lost` (67 + 147 = 214 against 153 real gap
seconds), which the residual cascade subtracts from `downtime` and hands to the other two
buckets. `unclassified_seconds` is structurally always 0, so the panel can never admit it does
not know.

**Read the panel as:** *"3,391 seconds of this session have no frame; ~3,239 of them because
capture wasn't running, ~153 because it restarted three times, and none because of a write
failure."*

### 16.2 Four metrics, three of which are labelled misleadingly

On one screen, for one session, the dashboard shows:

- `Frame loss 14.76%` (top KPI, red) — whole-day baseline
- `Missing 3,391s (14.69%)` — schedule-relative, the honest figure
- `Data loss 0.771% (incl. stale)` — process-relative
- `Elapsed loss 0.435% (gaps only)` — process-relative minus stale
- Per-underlying `Loss 0.44%` × 8 — yet another basis
- `feed healthy`, green — because at snapshot time the feed genuinely was healthy

The Session-history table's column is named **"Data loss"** and carries `data_loss_pct`, the
one metric that structurally cannot see an outage that shifts `first_grid_ms`. That is why
2026-08-28 appears as `0.77%` there and `14.76%` in the KPI. Both are "correct"; the naming
guarantees they will be compared.

Additionally: the KPI's *value* is `frame_loss_pct` while its *severity colour* is keyed to
`data_loss_pct`, and `lossSeverity` marks anything ≥1% as danger — so the tile can be red for a
reason unrelated to the number printed on it.

### 16.3 `stale_seconds` under-counts, `grid_seconds_lost` over-counts

`capture-<date>.json` is written by the Broadcaster on a **60 s throttle**
(`broadcaster.py:509-532`), and `persist_snapshot_now()` — which exists to force a final write
— **has no caller anywhere**. So on every escalation up to 60 s of `stale_seconds` /
`grid_gaps` / `grid_seconds_lost` is lost, and `_apply_carried` restores by **assignment**, not
`max`. With 3 restarts on 08-28 that is up to 180 s of stale accounting discarded — matching
the observed 67 vs ~105–120.

Conversely `grid_seconds_lost` counts the **whole** restart hole, including the suppressed
seconds already in `stale_seconds`. On 2026-08-27: `stale 305 + g_lost 430 = 735` against
`445` actually missing. The dashboard displays both raw ("Stale seconds 305", "Seconds lost
430"), so a reader summing them overstates by 65%.

### 16.4 Snapshot freshness and stale labelling

`capture-2026-08-28.json` shows `scheduled_seconds_elapsed 23,082` / `captures 19,691` while
the session-history row (written at capture stop) shows `23,100` / `19,708` — a 17–18 s
difference, again the throttle. Both are internally consistent; only the session-history row
is final.

On a Sunday the panel is still headed **"current session"** with **"Phase OPEN"**, rendering
Friday's snapshot as if live, while `/api/status` correctly reports
`market_phase: "HOLIDAY"`. The `/monitor` page never calls `/api/status`.

### 16.5 Counters not carried across a restart

`ticks_received`, `batches_received`, `dropped_batches`, `reconnects`, `token_refreshes` and
`unmatched` all reset per process and are **not** in the carried set (`bootstrap.py:271-286`).
Because `record_session_summary` **replaces** the row for a trading date
(`stats_store.py:214-217`), the surviving record is whichever generation stopped last. Hence
`Ticks/s` ranging 28.7 → 927.3 with no physical meaning.

### 16.6 Restart counts disagree three ways

Docker `RestartCount = 19`; `Started server process` appears **31** times in the logs since
2026-08-17; the escalation ledgers record **46** escalations (32 since 08-17); `capture task
failed` appears **29** times. No single source of truth for "how many times did we restart
today" is exposed anywhere.

---

## 17. Docker / VPS findings

**RULED OUT as a contributor.** Nothing correlates with any gap.

| Dimension | Measurement | Verdict |
|---|---|---|
| Memory | 14 Gi total, 4.2 Gi used, **10 Gi available**; swap 868 KiB of 4 Gi | No pressure |
| Backend container | **103.9 MiB / 14.57 GiB (0.70%)**, 16 PIDs | No pressure |
| CPU | 8 cores, load 0.54 / 0.48 / 0.39; container **0.15%** | No saturation |
| OOM | `OOMKilled=false`; 0 matches in kernel log | None |
| Disk | `/srv/data` 6.1 G / 1.8 T (**1%**), `/srv/dev_stack` 441 M / 458 G (1%) | None |
| Inodes | **1%** on every filesystem | None |
| Network | **0 errors, 0 dropped, 0 carrier** on `lo` and `enp2s0` | Clean |
| Restart policy | `unless-stopped`; last exit **code 0** (graceful SIGTERM) | As designed |
| Resource limits | `Memory=0`, `NanoCpus=0` — **no limits set** | No throttling |

The `ExitCode=0` is itself evidence: every restart was the intended
`CaptureStalledError → SIGTERM` path, not a crash, not a kill.

**Container clocks report UTC while the host reports IST** — a 5h30m offset that makes
`docker exec ls` timestamps look ~5.5 h earlier than reality, and `docker logs --since/--until`
interpret naive timestamps in **IST**. Both are investigation traps rather than defects.

---

## 18. EOD vs live completeness

Kept strictly separate, as required. EOD archiving is healthy and proves nothing about live
capture:

| Session | files | raw | zst | ratio | elapsed | throughput |
|---|---:|---:|---:|---:|---:|---:|
| 2026-08-26 | 8 | 8.99 GB | 0.27 GB | 33.36× | 374.6 s | 24.0 MB/s |
| 2026-08-27 | 8 | 8.94 GB | 0.28 GB | 31.79× | 390.2 s | 22.9 MB/s |
| 2026-08-28 | 8 | **7.77 GB** | 0.22 GB | 35.76× | 358.6 s | 21.7 MB/s |

39 sweeps recorded, 8 files every trading day (6 indices + `STOCKS` + `INDICES_FnO`), all
byte-for-byte verified before the raw was unlinked. 2026-08-28's smaller raw volume is exactly
consistent with its 54-minute late start.

**A verified archive proves the archive matches the raw file. It says nothing about whether
the raw file contained every owed second** — and on 2026-08-28 it demonstrably did not.

---

## 19. Confirmed root causes

### RC-1 — Second `KiteTicker` in a process is never serviced *(CONFIRMED, P0)*

Mechanism proven from SDK source and reproduced offline; production correlation exact at
38/38. Costs 1,817 s (29.1% of all loss) across the nine-session window, plus 5 fabricated
zero-filled frames × 8 artifacts per occurrence, plus — critically — it is the **generator of
RC-2**, because it forces a restart at 09:20 that changes the strike width behind an
already-written header.

Evidence: §10.1, `kiteconnect/ticker.py:496-537,551-560`, `app/kite/ticker.py:159-170`,
`proc.log` correlation table, offline loopback experiment, archive gaps at 09:15:09–09:15:12.

### RC-2 — Strike-axis misalignment in archived index chains *(CONFIRMED, P0)*

`write_header` sets the frame width before the header-once guard; the ATM window shifts across
restarts. 8 of 15 NIFTY sessions, ~128,110 frames, up to 99.98% of a day. Reproduced locally
and measured directly in the production archive. Undetected by every counter, test and
dashboard field.

Evidence: §15.2, `app/bin_codec/writer.py:205-212`, `app/chain/filter.py:71-75`, archive
`header_axis_len` vs `widths`, bootstrap strike-count log per generation.

### RC-3 — Calspread served a Kite-rejected token for 98 minutes *(CONFIRMED, P0, upstream)*

2026-08-28, 08:30:02 → 10:08:49 IST: `calspread.online/api/kite/token` returned HTTP 200 with
a token on every one of ~98 polls; `api.kite.trade/user/profile` returned **403 Forbidden**
every time. Capture could not start. 3,239 s = 51.9% of all loss in the window.

The TickVault-side defect is not the 403 — it is that **an auth failure during an open session
raises no alarm**: a 60 s `WARNING`, `/health` still 200, and the headline metric reading
0.77%.

Evidence: §8, `docker logs` 2026-08-28T03:00:02Z–04:38:49Z,
`app/session_service.py:_validate_broker_token`, `capture-2026-08-28.json` `first_grid_ms`.

### RC-4 — The loss metric family is mislabelled *(CONFIRMED, P2)*

Four denominators, the honest one not labelled "data loss", the archive-truth reconciler
(`ops/completeness.py`) present but never called, `stale_seconds` under-counting,
`grid_seconds_lost` over-counting, `unclassified_seconds` structurally always 0, and
"write fail" displayed for restart downtime. This did not cause data loss but it is why the
loss went un-diagnosed for weeks and why two incompatible figures were reported.

Evidence: §3, §16.

---

## 20. Highly likely contributors

- **HL-1 — Mid-day staleness is the SDK's internal reconnect losing races.** 22 mid-day
  escalations across nine sessions, each preceded by `feed stale (content unchanged for
  ~5000-5950ms)`. Since `TickerBridge.reconnect()` is unwired, the only in-process recovery is
  `kiteconnect`'s own retry, whose parameters TickVault never sets. Strong code and timing
  correlation; the SDK's internal state is not observable from the logs, so the specific
  trigger (half-open socket, broker-side disconnect, network blip) is not proven. The
  regularity of the intervals — 08-21 at 09:29:40 / 10:29:41 / 13:29:42, exactly 60:01 and
  180:01 apart — suggests a deterministic timer rather than random network loss.

- **HL-2 — The 300 s arm delay is a loss multiplier, not a safety net.** It converts a
  47-second detectable failure into a 295-second hole. Proven arithmetically and by the
  09:20:00-to-the-second escalations; classified "highly likely" rather than confirmed only
  because it is a *contributing* parameter, not an independent fault.

- **HL-3 — `CAPTURE_START_TIME == EQUITY_DERIV_OPEN` costs 5–8 s every session.** 55 s across
  nine sessions. The design intends the process to start earlier; `BOOTSTRAP_TIME=08:55` is set
  but has no consumer.

---

## 21. Possible but unconfirmed failure modes

Valid code paths with no evidence they occurred in this window:

- **P-1 — `set_mode` failing after a successful `subscribe`**, yielding reduced-mode ticks
  written as zero OI / zero depth. No evidence; `subscribe/set_mode failed` never appears.
- **P-2 — Silent partial subscription.** Kite may accept a subset without raising; nothing
  verifies coverage. `unmatched_ticks = 0` does not test for it (it detects the inverse).
- **P-3 — Dead writer thread stranding its queue.** `writer_lag_max = 0` throughout and the
  14/14 `captures` ↔ archive match argue it did not happen.
- **P-4 — Catch-up duplicate frames.** Ruled *out* empirically (`duplicate_timestamps = 0`)
  but the code path remains live for any future overload.
- **P-5 — A weekday exchange holiday reported as 100% loss.** `MARKET_HOLIDAYS` is empty.
  Certain to occur on the next holiday.
- **P-6 — Archive overwrite** by a post-EOD capture restart replacing a verified `.zst` with a
  shorter one. Second sweeps demonstrably happen (08-17, 08-20) but found nothing.
- **P-7 — A quiet whole-universe moment suppressing all 8 artifacts.** Mechanically possible
  at the 5 s threshold; not distinguishable in this window from RC-1/HL-1 windows.
- **P-8 — Process alive across midnight** writing to the previous date's paths. Not observed;
  the automation stops capture at 15:40.

---

## 22. Ruled-out causes

| Cause | Verdict | Evidence |
|---|---|---|
| 1 Hz scheduler drift | **RULED OUT** | Wall-clock-aligned `next_tick`; `off_grid_timestamps = 0` in all 17 archives |
| Duplicate / fabricated catch-up frames | **RULED OUT** | `duplicate_timestamps = 0` everywhere |
| Torn or corrupt frames | **RULED OUT** | `torn_prefix = torn_payload = 0`, including 5.51 GB files |
| Ingest queue overflow | **RULED OUT** | `dropped_batches = 0` in all 24 sessions |
| Instrument/token routing mismatch | **RULED OUT** | `unmatched_ticks = 0` in all 24 sessions |
| Partial resubscription after reconnect | **RULED OUT** | Immutable token list, full replay in `_on_connect`, full count in every log line |
| Writer backpressure / slow disk | **RULED OUT** | `writer_lag_max = 0`; unbounded queues; 12 s restart |
| Frames on disk not counted | **RULED OUT** | `captures` == archive frames, **14/14** sessions |
| Subscription-limit truncation | **RULED OUT** | 2,024–2,067 tokens vs safe limit 2,700 |
| Degraded/partial bootstrap | **RULED OUT** | All 6 chains + 210 stocks + 6×3 futures logged every generation; 8 artifacts always present with identical frame counts |
| Session-schedule misconfiguration | **RULED OUT** | 09:15–15:40 matches the post-2026-08-03 NSE structure; `unscheduled_seconds = 0` |
| Pre-open counted as owed | **RULED OUT** | Modelled 09:00–09:15, `capture_pre_open=False`, `scheduled_seconds = 23,100` |
| EOD / compression damaging data | **RULED OUT** | 39 verified sweeps, 8 files/day, raw removed only after byte-for-byte verify |
| Infrastructure pressure (CPU/RAM/disk/net/OOM) | **RULED OUT** | §17 — every dimension ≤1% utilised, zero errors |
| Frontend arithmetic error | **RULED OUT** | Rendered values match backend fields exactly; 0 console errors |
| Historical downloader interference | **RULED OUT** | `app/historical/` has no caller; no historical job in any log |
| Destructive token-refresh ladder | **RULED OUT** (fixed) | `token_refreshes = 0`; no `.invalidated-*` after 2026-07-31 |

---

## 23. Quantitative loss attribution

### 23.1 Per-artifact, 2026-08-28 (the ~15% session)

All eight artifacts are driven off one shared grid second, so their counts are identical —
verified independently in the archive for NIFTY, `INDICES_FnO` and `STOCKS`.

| Component | Expected s | Written frames | Missing | Missing % | Cause |
|---|---:|---:|---:|---:|---|
| NIFTY | 23,100 | 19,708 | 3,392 | 14.68% | late start + 3 restarts |
| BANKNIFTY | 23,100 | 19,708 | 3,392 | 14.68% | " |
| FINNIFTY | 23,100 | 19,708 | 3,392 | 14.68% | " |
| MIDCPNIFTY | 23,100 | 19,708 | 3,392 | 14.68% | " |
| SENSEX | 23,100 | 19,708 | 3,392 | 14.68% | " |
| BANKEX | 23,100 | 19,708 | 3,392 | 14.68% | " |
| STOCKS | 23,100 | 19,708 | 3,392 | 14.68% | " |
| INDICES_FnO | 23,100 | 19,708 | 3,392 | 14.68% | " |
| **Total** | **184,800** | **157,664** | **27,136** | **14.68%** | |

Decomposition for that session:

```
incorrect session expectation        0 s   (schedule is correct)
intentional stale suppression       ~5 s   (the 5 junk-frame window)  [reported as 67]
restart downtime                   153 s   (3 restarts: 47 + 49 + 57)
late startup                     3,239 s   (Calspread 403 outage)
early shutdown                       0 s   (last frame 15:39:59)
subscription failure                 0 s
writer / persistence loss            0 s
unexplained                          0 s
                                 -------
                                 3,392 s   = 14.68%
```

### 23.2 Nine-session aggregate (2026-08-18 … 2026-08-28)

Owed: 9 × 23,100 = **207,900** artifact-seconds. Missing: **6,246** = **3.005%**.

| Cause | Seconds | Share of loss | Share of owed | Sessions affected |
|---|---:|---:|---:|---:|
| Late startup — Calspread token rejected (RC-3) | 3,239 | 51.9% | 1.558% | 1 |
| Dead first ticker (RC-1) | 1,817 | 29.1% | 0.874% | 7 |
| Mid-day stale → restart cycles (HL-1) | 1,135 | 18.2% | 0.546% | 8 |
| Bootstrap head loss at the open (HL-3) | 55 | 0.9% | 0.026% | 9 |
| Incorrect session expectation | 0 | 0.0% | 0.000% | 0 |
| Early shutdown | 0 | 0.0% | 0.000% | 0 |
| Subscription failure | 0 | 0.0% | 0.000% | 0 |
| Writer / persistence loss | 0 | 0.0% | 0.000% | 0 |
| **Unexplained** | **0** | **0.0%** | **0.000%** | **0** |
| **Total** | **6,246** | **100%** | **3.005%** | |

Counterfactuals:

- Without the 2026-08-28 auth outage: **1.447%**
- Without the auth outage *and* RC-1: **0.573%**
- Without all three (auth, RC-1, mid-day restarts): **0.026%** — i.e. bootstrap head loss only

### 23.3 Per-session reconciliation against the archive

Independent verification that the attribution is complete: for every session,
`archive gaps + (first frame − session open) = reported missing_seconds`.

| Session | archive miss | head loss | sum | reported | ✓ |
|---|---:|---:|---:|---:|:--:|
| 08-11 | 1,380 | 306 | 1,686 | 1,686 | ✅ |
| 08-12 | 67 | 8 | 75 | 75 | ✅ |
| 08-13 | 676 | 6 | 682 | 682 | ✅ |
| 08-14 | 440 | 5 | 445 | 445 | ✅ |
| 08-17 | 202 | 6 | 208 | 208 | ✅ |
| 08-18 | 245 | 8 | 253 | 253 | ✅ |
| 08-19 | 389 | 7 | 396 | 396 | ✅ |
| 08-20 | 414 | 6 | 420 | 420 | ✅ |
| 08-21 | 142 | 7 | 149 | 149 | ✅ |
| 08-24 | 343 | 7 | 350 | 350 | ✅ |
| 08-25 | 532 | 8 | 540 | 540 | ✅ |
| 08-26 | 296 | 5 | 301 | 301 | ✅ |
| 08-27 | 438 | 7 | 445 | 445 | ✅ |
| 08-28 | 153 | 3,239 | 3,392 | 3,392 | ✅ |

**14/14 exact.** Every missing second in the archive is accounted for by an identified cause.


---

## 24. Proposed fixes — P0 / P1 / P2 / P3

Design only. Nothing below has been implemented.

### P0-1 — Make the ticker connection verifiable and never silently dead

**Root cause:** RC-1. `KiteTicker.connect(threaded=True)` is a no-op for the second instance
in a process, and `TickerBridge.connected` is never read.

**Proposed behaviour**, in ascending order of intrusiveness — implement (a) and (b) together;
(c) is the durable fix:

- **(a) Assert the connection.** After `bridge.start()`, await `on_connect` with a bounded
  timeout (~10 s). On timeout, raise `TickerConnectFailed` rather than proceeding into a grid
  that will write junk. This alone converts a 295 s silent hole into a ~10 s loud failure.
- **(b) Drive the reactor correctly.** Start the Twisted reactor **once per process** and use
  `reactor.callFromThread(connectWS, factory, …)` for every subsequent ticker, instead of
  relying on `connect()`'s `if not reactor.running` branch. Equivalent and simpler
  alternative: keep one long-lived `KiteTicker` for the process lifetime and re-`subscribe`
  rather than reconstructing it.
- **(c) Guarantee a virgin reactor per capture session.** Make the capture session's exit path
  always terminate the process (or run the ticker in a dedicated child process), so
  "generation 2 in a process" cannot exist. This is the same direction as
  `plans/distributed-capture-architecture-plan.md`'s fetcher split and should be the strategic
  target.
- Also: read `bridge.connected` in `CaptureMonitor` and surface it per artifact.

**Failure mode prevented:** a fully dead feed that looks healthy for 295 s and writes
fabricated frames.
**Deployment risk:** medium. (a) is low-risk and independently valuable; (b) touches SDK
interaction and needs a soak test; (c) changes the process model.

### P0-2 — Never write frames before the first real tick

**Root cause:** RC-1's side effect — `FreshnessMonitor` measures from `_started_ms` when
`last_change_ms is None`, so seconds 1–5 of a dead feed are "fresh" and 5 all-zero frames
(× 8 artifacts) are written and archived.

**Proposed behaviour:** treat "no tick has ever arrived" as a distinct state from "ticks have
gone stale". Suppress writes until the first tick is observed, and count those seconds as
`awaiting_first_tick_seconds` rather than as captures.

**Failure mode prevented:** zero-filled frames indistinguishable from real all-zero market
state, and — critically — a **header written from a garbage generation** (the mechanism that
makes RC-2 catastrophic on 08-26/08-27).
**Deployment risk:** low. Narrow, well-defined condition.

### P0-3 — Bind the frame width to the header, and detect divergence

**Root cause:** RC-2. `write_header` sets `self._n_strikes` before the header-once guard.

**Proposed behaviour:**

- On reopen, **read the axis back from the existing header** and use *that* as the frame width;
  if the in-memory table's axis differs, refuse to append and fail loudly.
- Preferred over refusing: **roll to a new file segment** (e.g. `<date>.p2.bin`) with its own
  header when the axis changes, so a shifted ATM window is recorded honestly instead of being
  crammed onto a stale axis. This preserves the append-only property and needs a reader that
  can chain segments.
- Minimum viable alternative if neither is acceptable: **stamp each frame with its own axis
  identity** (e.g. first/last strike in paise) so misalignment is at least detectable on read.
- Add an invariant check in `scan_frames`/`reader` that flags `frame_axis_len !=
  header_axis_len`.

**Failure mode prevented:** silent mis-attribution of option data to the wrong strikes, which
also corrupts every downstream reconstruction (IV, Greeks, max-pain, PCR all take strike as an
input).
**Deployment risk:** medium — it is a change to the binary contract's operational semantics.
Segment-rolling is backward compatible for existing files; refusing-to-append is not (it turns
today's silent corruption into a visible capture stop).

### P0-4 — Alarm on "session open, frames owed, capture not running"

**Root cause:** RC-3's blast radius. A 98-minute auth outage produced only 60 s `WARNING`s
while `/health` stayed 200 and the headline metric read 0.77%.

**Proposed behaviour:**

- Introduce an explicit `AUTH_BLOCKED` state (already specified in
  `docs/60-operations/broker-session-authentication.md`) that is entered when a broker token is
  fetched and rejected, and surface it in `/health` as **degraded**, escalating to **dead**
  once `now > session_open + grace` with capture still not running.
- Distinguish, in logs and telemetry, "Calspread has no session" from "Calspread's session was
  rejected by Kite (403)" — the second is an administrator action item, the first is a wait.
- Emit one aggregated alert, not a per-poll `WARNING`.

**Failure mode prevented:** losing 54 minutes of a trading day with nobody notified.
**Deployment risk:** low. Read-only status surface plus log-level changes.

### P1-1 — Restore an in-process reconnect rung

**Root cause:** HL-1 + §13.4. Recovery has only "SDK internal retry" and "kill the process";
22 of the window's restarts were mid-day staleness that a socket replacement would likely have
fixed for ~2 s instead of ~48 s.

**Proposed behaviour:** wire the existing, tested `TickerBridge.reconnect()` behind
`ReconnectPolicy` as a rung *before* escalation — e.g. at `CAPTURE_STALE_SECONDS × 2`, with at
most N attempts per hour, then escalate as today. Must be built on P0-1(b), otherwise the
replacement ticker hits exactly RC-1. Also set `reconnect_max_tries` / `reconnect_max_delay`
explicitly on `KiteTicker` instead of inheriting SDK defaults.

**Failure mode prevented:** whole-process restarts for a recoverable socket fault.
**Deployment risk:** medium — this path is currently dormant; it needs the RC-1 fix first and a
guard against reconnect storms.

### P1-2 — Decouple the recovery arm delay from `open_at`

**Root cause:** HL-2. Arming at `open + 300 s` means a failure at the open cannot be acted on
for 295 s.

**Proposed behaviour:** arm on **evidence, not elapsed time** — as soon as the first tick of
the session has been observed, recovery is armed. Keep a shorter absolute floor (30–60 s) for
the genuinely silent pre-open case. With P0-1(a) in place the arm delay stops being the
binding constraint at all.

**Failure mode prevented:** the 295 s → 47 s reduction on every affected morning.
**Deployment risk:** low, but it interacts with pre-open semantics — must not re-create the
2026-08-04/05/06 pre-open restart storms.

### P1-3 — Per-artifact freshness must gate writes, not just logs

**Root cause:** §11.2. One live instrument (notably India VIX, which fans out to all six index
tables) masks a dead dataset; conversely one aggregate quiet moment suppresses all eight.

**Proposed behaviour:** make suppression a **per-artifact** decision using
`artifact_last_update_ms`, so a frozen artifact stops being written while healthy ones
continue. Exclude the VIX fan-out from an index artifact's liveness evidence.

**Failure mode prevented:** frames full of duplicated last-known values for a dead subset,
counted as successful captures — the one remaining way this system can lose data while
reporting health.
**Deployment risk:** medium — changes which seconds land in which file, so the completeness
model and its tests must move together.

### P1-4 — Persist telemetry on stop and on escalation

**Root cause:** §16.3. `persist_snapshot_now()` has no caller; the 60 s throttle discards up to
60 s of stale/gap accounting per restart, and `_apply_carried` assigns rather than maxes.

**Proposed behaviour:** call `persist_snapshot_now()` in the engine's `finally` and immediately
before `_escalate_stale` raises. Carry counters with `max(carried, current)`.

**Deployment risk:** low.

### P2-1 — One authoritative completeness number, from the archive

**Root cause:** RC-4. Four metrics, the honest one not called "data loss", and
`ops/completeness.py` unused.

**Proposed behaviour:**

- **Wire `ops/completeness.reconcile()`** into the EOD path: after compression, reconcile the
  archive's distinct second-slots against the session schedule and record the result as the
  session's canonical `data_loss_pct`. This is the only figure that cannot be fooled by a
  counter, and it needs no live plumbing.
- **Rename the UI fields** so the denominator is explicit: "Missing vs schedule" (authoritative),
  "Missing vs observed window" (diagnostic), "Day progress" (not loss). Stop labelling any
  process-relative figure "Data loss".
- Fix the Session-history column to carry the schedule-relative figure.
- Key KPI severity to the metric printed on the tile.

**Deployment risk:** low — additive at EOD, plus labelling.

### P2-2 — Fix cause attribution and stop double-counting

**Root cause:** §16.1, §16.3. "write fail 147s" is restart downtime; `stale_seconds +
grid_seconds_lost` overstates the real hole by ~65%.

**Proposed behaviour:** account each missing second **once**, at the moment it is missed, into
a single tagged bucket (`awaiting_first_tick`, `stale_suppressed`, `restart_downtime`,
`not_running`, `write_failed`, `unknown`). Remove the residual cascade so
`unclassified_seconds` can be non-zero and therefore meaningful. Stop adding restart downtime
to `grid_seconds_lost`; give it its own counter.

**Deployment risk:** low. Telemetry-only.

### P2-3 — Close the remaining accounting gaps

- Populate `MARKET_HOLIDAYS` for the remainder of 2026 (P-5 is otherwise certain on the next
  holiday), or fetch the calendar.
- Include `index_fno_writer` in `resume_from_disk`'s writer list; use per-artifact frame counts
  rather than `max()`.
- Fix `downtime_s`'s off-by-one; fix `scan_frames` to report a payload-truncated frame as
  truncated and not count it.
- Make `record_session_summary` append per generation, or aggregate rather than replace.
- Either honour `BOOTSTRAP_TIME` / set `CAPTURE_START_TIME=09:10`, or delete the dead setting.
- Make `EXPECTED_FRAMES_PER_SESSION` either effective or removed; never resurrect a baseline
  for a disabled session.
- Refuse to `os.replace` an existing verified archive with a shorter same-date file.

### P3-1 — Observability so the next "5%" is self-explaining

See §27.

---

## 25. Exact files expected to change

| Fix | Files |
|---|---|
| P0-1 | `backend/app/kite/ticker.py` (`_start_ticker`, `start`, `reconnect`, add a connect-confirmed event); `backend/app/capture/bootstrap.py` (`run_capture` awaits confirmation); `backend/app/capture/monitor.py` (surface `bridge.connected`); possibly `backend/app/api/capture.py` (fatal-handler semantics for `TickerConnectFailed`) |
| P0-2 | `backend/app/capture/reconnect.py` (`FreshnessMonitor`: distinguish never-ticked); `backend/app/capture/engine.py` (`capture_snapshot` suppression branch, new counter) |
| P0-3 | `backend/app/bin_codec/writer.py` (`IndexBinWriter/StockBinWriter/IndexFnoBinWriter.write_header` + `append_frame`); `backend/app/bin_codec/reader.py` and `scan.py` (axis-divergence detection); `backend/app/capture/engine.py` (`build_*_writer` if segment-rolling); `docs/20-data-and-storage/bin-structure-spec.md` |
| P0-4 | `backend/app/session_service.py` (`AUTH_BLOCKED` state, distinguish 403 from no-session); `backend/app/ops/automation.py` (surface the blocked state, aggregate logging); `backend/app/api/health.py` (degraded/dead escalation); `backend/app/api/status.py` |
| P1-1 | `backend/app/capture/engine.py` (`observe_feed_health` ladder); `backend/app/capture/reconnect.py` (`ReconnectPolicy` wiring); `backend/app/kite/ticker.py` (explicit `reconnect_max_tries`/`reconnect_max_delay`) |
| P1-2 | `backend/app/ops/sessions.py` (`is_stale_armed_at`); `backend/app/config.py` (arm-on-first-tick semantics + floor) |
| P1-3 | `backend/app/capture/engine.py` (per-artifact suppression); `backend/app/capture/feed_health.py`; `backend/app/capture/monitor.py` |
| P1-4 | `backend/app/capture/broadcaster.py` (`persist_snapshot_now` callers); `backend/app/capture/engine.py` (`_escalate_stale`, `finally`, `_apply_carried` → max) |
| P2-1 | `backend/app/ops/completeness.py` (wire it); `backend/app/ops/eod.py`; `backend/app/ops/stats_store.py`; `frontend/lib/monitor/viewModel.ts`; `frontend/components/monitor/DataLossDiagnostics.tsx`, `SessionHistory.tsx`, `MonitorKpis.tsx`, `FrameIntegrity.tsx`; `frontend/lib/monitor/severity.ts` |
| P2-2 | `backend/app/capture/engine.py` (tagged counters); `backend/app/capture/monitor.py` (`_loss_breakdown`); `backend/app/ws/protocol.py` |
| P2-3 | `backend/app/config.py`; `backend/app/capture/engine.py` (`resume_from_disk`); `backend/app/bin_codec/scan.py`; `backend/app/bin_codec/compress.py`; `backend/app/ops/stats_store.py`; `DATA_DOWNLOADER/.env` (holidays, `CAPTURE_START_TIME`) |
| P3-1 | `backend/app/capture/monitor.py`; `backend/app/ws/protocol.py`; `backend/app/api/status.py`; frontend monitor components |

---

## 26. Tests required

Every test below targets a defect actually observed in production, not a hypothetical.

**RC-1 — ticker connection**
1. A second `TickerBridge` in one process must either connect or raise within the timeout —
   never return silently unconnected. (Fake ticker factory; assert `on_connect` observed or
   `TickerConnectFailed`.)
2. `run_capture` must not enter the 1 Hz grid before the connection is confirmed.
3. A connect timeout must be surfaced as a capture failure with a distinct exception type.
4. Regression test with a factory that mimics the SDK's silent no-op (never calls
   `on_connect`, never raises) — this is exactly the production shape and currently passes
   undetected.

**RC-2 — strike axis**
5. Reopening an index file whose in-memory strike count differs from the on-disk header must
   **not** append frames on the stale axis. Assert the chosen policy (raise, or roll a new
   segment).
6. Every frame's decoded column length must equal the axis its header declares, for a file
   written across ≥3 generations with 3 different strike counts.
7. A reader/scan invariant test that flags axis divergence in a deliberately corrupted file
   (guards the detector itself).
8. Property test: `option_chain_filter` window shift as spot moves must be reflected in a
   header-visible way.

**RC-1/P0-2 — pre-first-tick**
9. A capture session that receives no ticks must write **zero** frames (currently writes 5).
10. Those seconds must be counted as `awaiting_first_tick`, not as stale and not as captures.

**RC-3 — auth blocked**
11. Broker returns 200 with a token that fails validation → `AUTH_BLOCKED`, no capture start,
    no token persisted, one aggregated alert (not one per poll).
12. `AUTH_BLOCKED` past `session_open + grace` → `/health` reports **dead** and returns 503.
13. Distinguish "no session at broker" from "broker session rejected by Kite (403)".

**Accounting**
14. A session where the process starts 54 minutes late must report ~14.7% on the authoritative
    metric, and must **not** report ~0.8% on any field labelled "data loss".
15. `ops/completeness.reconcile()` run against a synthetic archive with known holes must
    reproduce the exact missing-second count, and must be invoked by the EOD path.
16. `stale_seconds + restart_downtime` must equal the archive's gap seconds exactly — no
    double-count (today: 305 + 430 vs 445 actual).
17. `unclassified_seconds` must be able to be non-zero.
18. Counter carry-over across a restart must use `max`, and a forced snapshot must be written
    before escalation raises.
19. A weekday entry in `MARKET_HOLIDAYS` must produce zero owed seconds and no `START_CAPTURE`.

**Recovery**
20. Restart downtime assertion must be re-baselined: `test_restart_resume.py:120` asserts
    25–30 s; production measures ≈12 s.
21. An in-process reconnect (P1-1) must recover a stalled feed without a process exit, and must
    not storm.
22. Recovery armed on first-tick evidence must not arm during a silent pre-open.

**Gap between tested-synthetic and real-provider behaviour** — the four things CI cannot see
today, all of which bit production:
- the SDK's global reactor lifecycle across two tickers in one process (RC-1);
- an accepted-but-unusable broker token (RC-3);
- a real `subscribe` that is accepted asynchronously without acknowledgement (P-2);
- real strike-list clipping as spot moves across a restart (RC-2).

Each needs either an SDK-fidelity fake or a nightly credentialed soak run; the current fakes
are too well-behaved to express any of them.

---

## 27. Production verification plan

Read-only checks first, then a staged rollout. No production mutation was performed during
this investigation and none is required to verify.

**Pre-deploy baseline (already captured, reusable):** the 24-row session history, the 46
escalation timestamps, and the 17-archive frame scan in `/tmp/tv_forensics/`. `binscan.py` is
dependency-light and validated against the project's own writer — keep it as the independent
oracle.

**Verification for P0-1 + P0-2 (the daily morning hole):**
1. On the first trading day after deploy, confirm from the logs that **every** `capture started`
   line is followed within ~2 s by `ticker connected; subscribing N tokens`.
2. Confirm the archive has **no 290–300 s gap starting at 09:15:0x**:
   `binscan.py index::<archive>/INDICES/NIFTY/<date>.bin.zst` → `largest_gaps` must contain no
   ~295 s entry.
3. Confirm the first frame timestamp is ≥09:15:00 and the file contains **no all-zero opening
   frames** (frame count at the first width should no longer be exactly 5).
4. Confirm `escalations-<date>.json` contains **no 09:20:00 entry**.
5. Expected effect: `scheduled_loss_pct` on a clean day drops from ~1.3–1.9% to ~0.2–0.6%.

**Verification for P0-3 (strike axis):**
6. `binscan.py` on each index archive: `widths` must be a **single** value equal to
   `header_axis_len` — or, with segment-rolling, each segment's frames must match its own
   header.
7. Deliberately force a restart on a day when NIFTY's window is clipped (a maintenance-lease
   drain is the non-destructive way) and confirm the file self-describes correctly.

**Verification for P0-4 (auth alarm):**
8. Re-run the 403 condition in staging against a mock broker returning 200-with-bad-token;
   confirm `/health` goes degraded then 503, and that exactly one alert is raised.
9. Do **not** reproduce this against production Calspread.

**Verification for P2-1 (metric):**
10. After the next EOD, compare the archive-derived `data_loss_pct` from
    `ops/completeness.reconcile()` with my independent `binscan.py` figure for the same file.
    They must agree to the second.
11. Confirm the dashboard shows one authoritative loss number, and that no field labelled "data
    loss" is process-relative.

**Backfill / remediation of existing archives (separate, non-destructive task):**
12. For the 8 affected NIFTY sessions, recompute each generation's true strike window from
    `_instruments/<date>/NFO.csv` + each frame's `spot_price` + the `sequence` reset boundaries,
    and emit a **sidecar** axis map rather than rewriting any `.bin.zst`.
13. Treat 2026-08-26 and 2026-08-27 NIFTY as **unusable for strike-indexed analysis** until that
    sidecar exists.

**Rollout shape:** P0-4 and P1-4 and P2-2 are telemetry/status-only — ship first. P0-1(a) and
P0-2 next, as they are narrow and immediately measurable by check 1–4. P0-3 last within P0,
because it touches the storage contract and warrants a full day of observation. Use the
existing release-drain maintenance lease so no session is truncated mid-write, and keep
`rollback.sh` ready — the bundle supports it without a rebuild.

---

## 28. Observability improvements

The goal: the next "5% loss" report should be answerable from one screen without an
investigation. Currently missing, each mapped to a specific blind spot this investigation hit:

| Metric | Why — what it would have revealed |
|---|---|
| `ticker_connected` (bool) + `ws_connect_epoch` + `ws_connect_attempts` | RC-1 directly. `bridge.connected` exists and is never surfaced or read. |
| `awaiting_first_tick_seconds` | Would separate the 5 junk frames and the 295 s hole from ordinary staleness. |
| `capture_generation` / `process_epoch` per session | Restart count is currently guessable only from `sequence` resets in the archive; Docker says 19, logs say 31, ledger says 46. |
| `last_tick_ms` **and** `last_content_change_ms` (separately, per artifact) | Distinguishes a dead transport from a quiet market from a frozen subset. |
| `subscribed_token_count` **as acknowledged**, vs planned | P-2. Today only the planned count exists. |
| `tokens_producing_ticks` (distinct tokens seen in the last N s) | Would expose a dead subset that per-artifact ages cannot see. |
| `writer_thread_alive` per artifact | `is_alive()` is called nowhere except `stop`. |
| `expected_scheduled_second` vs `last_written_second` per artifact | Makes per-artifact divergence visible instead of pooled. |
| `frame_axis_len` vs `header_axis_len` per artifact | RC-2 — the single most valuable new check. |
| `auth_state` (`OK` / `NO_SESSION` / `AUTH_BLOCKED`) + `auth_blocked_seconds` | RC-3. Would have made the 98-minute outage a first-class alarm. |
| `not_running_seconds` while the session is open | The 3,239 s that `data_loss_pct` structurally cannot see. |
| Tagged, single-counted loss buckets (§P2-2) | Replaces "write fail 147s" for something that was not a write failure. |
| `archive_reconciled_loss_pct` (from `completeness.reconcile`) at EOD | One authoritative number per session. |
| `restart_gap_seconds` as its own counter | Stops `grid_seconds_lost` double-counting the stale prelude. |

Two log-hygiene items that materially slowed this investigation:

- **`/health` and the dashboard polls flood the container log.** With `max-size: 10m,
  max-file: 3`, a 20 s healthcheck plus 15 s UI polling rotates real capture logs out within
  days. Aug 28's incident survived only narrowly. Either raise retention, drop access logs for
  `/health` and `/api/*`, or ship logs off-box.
- **The container reports UTC while the host reports IST**, and `docker logs --since` parses
  naive timestamps as IST. Worth a line in the runbook.

---

## 29. Remaining unknowns

1. **What triggers the 22 mid-day stale spells (HL-1).** The escalations are suspiciously
   periodic (2026-08-21 at 09:29:40 / 10:29:41 / 13:29:42 — exactly 60:01 and 180:01 apart;
   2026-08-25 at 10:40:14 / 10:55:17 — 15:03 apart), which points at a deterministic timer
   inside `kiteconnect`/autobahn rather than random network loss. The SDK's internal state is
   not logged, so this is not proven. Enabling `KiteTicker(debug=True)` or Twisted logging for
   one session would settle it.
2. **Why 2026-08-10 lost 3,205 s.** Same signature as 08-28 (`data_loss_pct = 0.00`,
   `scheduled_loss_pct = 14.24`), but it predates the retained logs (which start 2026-08-17
   15:45). It was the 6-index/`INDICES_FnO` rollout day, so a deploy window is the likely
   explanation — unconfirmed.
3. **Why Calspread's token was 403-rejected for 98 minutes.** Determined from TickVault's side
   (Calspread served it, Kite refused it), but the upstream cause is outside this repo and
   requires the Calspread logs.
4. **Whether `set_mode` has ever silently failed after a successful `subscribe`** (P-1),
   writing zero-OI/zero-depth frames. Detectable retrospectively: scan an archive for frames
   where every leg's OI and depth are zero while LTP moves. Not attempted here.
5. **The true magnitude of the strike misalignment.** I proved the frame widths disagree with
   the header and that the window shifts; I did not reconstruct each generation's actual strike
   list, so the per-frame *offset* (and therefore the exact reconstruction error) is not
   quantified. This requires the `_instruments` replay described in §27.12.
6. **Reconciling three restart counts** (Docker 19, log `Started server process` 31, escalation
   ledger 46). Likely a mix of container recreation on 08-17 and pre-08-17 ledger entries, but
   not resolved.
7. **Whether the 15:15–15:40 structurally frozen spot legs are being recorded as
   last-traded-value repeats or as genuine CAS prints.** The session model treats all eight
   artifacts as one derivatives session; the archive was not inspected column-wise for that
   window.
8. **`git_dirty: true` in the deployed manifest** — the running image was built from a working
   tree with uncommitted changes, so the deployed code is not exactly `61efd8d`. Every
   behaviour in this report was verified against the deployed container or the archive it
   produced, but a source-level diff of the image against the commit was not performed.

---

## Answers to the seven required questions

**1. Is the reported 3–5% actually 3–5% genuine market-data loss?**
The seconds are genuinely missing — but the two numbers being quoted are different metrics.
Across the nine sessions since 2026-08-18 the aggregate is **3.005%** of owed artifact-seconds
with **zero unexplained residual**, and my independent archive scan reconciles with the
backend's own `missing_seconds` in **14 of 14** sessions. So: yes, real, and now fully
attributed. What is *not* real is the implication that the same quantity was 3–5% and then
15%; the "3–5%" figures are the Session-history `data_loss_pct` column and the "15%" is
`frame_loss_pct`/`scheduled_loss_pct`.

**2. How much is caused by wrong expected-session accounting?**
**Zero.** The deployed schedule (09:15–15:40, pre-open modelled and excluded,
`scheduled_seconds = 23,100`, `unscheduled_seconds = 0`) correctly matches the post-2026-08-03
NSE derivatives structure. The `F1` finding in the distributed-capture plan has already been
remediated in the deployed `.env`. The accounting defect is one of *labelling and denominator
choice*, not of expected seconds — plus one latent time bomb: `MARKET_HOLIDAYS` is empty, so
the next weekday exchange holiday will report 100%.

**3. How much is deliberate stale-write suppression?**
Small and mostly a symptom rather than a cause. Reported `stale_seconds` is 65–342 on healthy
sessions (0.3–1.5% of the day), and it is an **under-count** of the true suppressed seconds
because the 60 s snapshot throttle discards accounting on every restart. Critically, almost all
of it is *consequential*: the suppressed seconds are the windows created by RC-1 and by each
pre-escalation spell, not independent quiet-market false positives. Suppression itself is
correct and should not be relaxed.

**4. How much is process/reconnect downtime?**
**2,952 s of 6,246 = 47.3%** of all missing seconds (RC-1's 1,817 s plus 1,135 s of mid-day
restart cycles), from 29 escalation-driven restarts across nine sessions. Measured restart cost
is **≈12 s**, not the documented 25–30 s; the expensive part is *detection* (35 s at
`CAPTURE_STALE_EXIT_SECONDS=40`, or 295 s when the arm delay is in force).

**5. Is any data being lost while the feed is otherwise healthy?**
**No missing seconds — but yes, corrupted data.** With a healthy feed the pipeline is clean:
`dropped_batches = 0`, `unmatched_ticks = 0`, `writer_lag_max = 0`, 0 torn frames, 0 duplicate
timestamps, 0 off-grid timestamps, and `captures` == frames-on-disk in 14/14 sessions. However
**RC-2 silently corrupts data that was captured perfectly**: on 8 of 15 NIFTY sessions the
frames' strike axis disagrees with the file header (99.98% of the day on 08-26 and 08-27,
~128,110 frames total). That is loss of *meaning* rather than loss of *seconds*, it happens
while every health indicator is green, and it is the finding I would act on first after
stopping the restarts that cause it.

**6. What specifically caused the ~15% session?**
2026-08-28: Calspread returned HTTP 200 with a Kite access token on every 60-second poll from
08:30:02 IST, and Kite rejected it with **403 Forbidden** every time, until 10:08:49. Capture
therefore could not start until 10:08:59 — **3,239 seconds (54 minutes) after the session
opened**, which is 14.0 of the 14.68 percentage points. The remaining 153 s were three
escalation restarts (47 + 49 + 57 s), the first of which was RC-1 firing on the freshly
acquired token. Once running, the process captured 19,708 of the 19,861 grid seconds it
observed — 99.23%. It was one upstream authentication outage, not a capture-pipeline fault, and
TickVault's own defect was failing to alarm on it.

**7. What exact changes will reduce genuine unexplained loss as close to zero as practically
possible?**
Unexplained loss is already **0 s**; the work is eliminating the *explained* loss and the silent
corruption. In dependency order:

- **P0-1** — confirm the ticker connection with a bounded timeout and drive the reactor
  correctly (or guarantee a virgin process per capture session). Removes 1,817 s (29.1%) and,
  by removing the 09:20 restart, removes the mechanism that makes RC-2 catastrophic.
- **P0-2** — never write frames before the first real tick. Removes 40 fabricated frames per
  occurrence and stops a garbage generation from writing the header.
- **P0-3** — bind frame width to the on-disk header, or roll a new segment when the axis
  changes, and add an axis-divergence invariant. Stops RC-2 permanently.
- **P0-4** — an `AUTH_BLOCKED` state that reaches `/health` and one aggregated alert. Turns a
  future 3,239 s outage into a paged incident within a minute.
- **P1-1 / P1-2** — restore an in-process reconnect rung and arm recovery on first-tick
  evidence instead of `open + 300 s`. Converts most of the remaining 1,135 s into ~2 s
  recoveries.
- **P2-1 / P2-2** — wire `ops/completeness.reconcile()` at EOD as the single authoritative
  number, rename the process-relative metrics, and single-count each missing second into a
  tagged bucket.

With P0-1 through P0-4 and P1-1/P1-2 in place, the expected residual is bootstrap head loss
only — **≈0.03% of owed seconds** (55 s over nine sessions), reducible to ~0 by starting the
capture window a few minutes before the session as the design already intends.

---

## Appendix — evidence artifacts

Collected under `/tmp/tv_forensics/` (working directory, not committed):

| File | Contents |
|---|---|
| `session-history.jsonl` | 24 session summaries pulled from the deployed stats store |
| `escalations.txt` | All 14 per-date escalation ledgers, 46 timestamps |
| `all-captures.txt` | All 26 `capture-<date>.json` monitor snapshots |
| `capture-2026-08-27.json`, `capture-2026-08-28.json` | Per-artifact detail for the comparison sessions |
| `compression-history.jsonl` | 39 EOD sweeps |
| `events.log`, `proc.log`, `backend.log` | Filtered and raw `docker logs` extracts |
| `binscan.py` | Dependency-light `.bin`/`.bin.zst` forensic parser, self-tested against the project's own writer |
| `archive_scan_1.json`, `archive_scan_2.json`, `archive_scan_stocks.json` | Frame-level scans of 17 production archives |
| `dash.mjs`, `monitor-text.txt`, `monitor-full.png`, `monitor-viewport.png`, `api-capture.json` | Headed Playwright capture of the deployed dashboard |

Read-only methods used: `ssh beonedge`, `docker inspect`, `docker logs`,
`docker exec … cat/ls`, `docker exec -i … python3 -` (parser piped via stdin; nothing written
into the container), headed Chromium against `/monitor`, and one offline loopback experiment
against `127.0.0.1:9`. No production file, container, configuration, or archive was modified.


---

## 30. Implementation record — 2026-08-30

What was actually built, per proposed fix. Nothing is deployed yet.

### P0-1 — the ticker connection is now real and verifiable *(RC-1)*

`app/kite/ticker.py`
* `_connect_ticker(ticker)` — **the fix**. When the process-global Twisted reactor is already
  running, the connect is performed *on the reactor thread* via `reactor.callFromThread`
  instead of being called into a live reactor from the main thread. Falls back to a plain
  threaded connect when Twisted is absent (test fakes).
* `TickerConnectFailed`, `CONNECT_TIMEOUT_S = 15`, `connect_confirmed` (an `asyncio.Event`
  set from `_on_connect` via `call_soon_threadsafe`, cleared on every `_start_ticker`),
  `await_connected()`, and `connect_attempts` / `connects_confirmed` / `last_connect_ms`.
* `_default_ticker_factory` now pins `reconnect_max_tries = 5` and `reconnect_max_delay = 10`
  rather than inheriting whatever the SDK defaults to.

`app/capture/bootstrap.py`
* `run_capture` awaits the new `_await_live_feed()` **before** creating the engine task, so
  the 1 Hz grid is never entered on an unconfirmed socket. It races `connect_confirmed`
  against `auth_failed` so a rejected token is reported as an auth failure, not a timeout.
* `CaptureContext.bridge` is typed `TickerBridge` instead of `object` (this removed 7
  pre-existing mypy errors).

`app/capture/monitor.py` — `ticker_connected`, `ws_connect_attempts`,
`ws_connects_confirmed`, `ws_last_connect_ms` are surfaced. `bridge.connected` existed and
was never read by anything; that is why a socket which never came up looked healthy.

### P0-2 — no frame is written before the first real tick

`app/capture/reconnect.py` — `FreshnessMonitor.has_ticked`.
`app/capture/engine.py` — `capture_snapshot` suppresses while `not has_received_data()`;
new `awaiting_first_tick_seconds` counter and its own log line; `_ticks_ever_applied` set in
`apply_ticks`, and `has_received_data()` checks **both** entry points so the gate cannot be
bypassed by a caller that only exercises one. The gate is **unconditional** — it is not
governed by `CAPTURE_SUPPRESS_STALE_WRITES`, because an all-zero table is fabricated from
nothing rather than a frozen quote.

`app/capture/writer_thread.py` — the header is now flushed **lazily, on the first frame**
(`bind_header` at startup to validate the file and surface an axis conflict, `flush_header`
on first append). `_BaseWriter.close()` unlinks a 0-byte file, so a generation that captured
nothing leaves no trace at all — previously it left the header that then defined the file's
axis for the whole day.

### P0-3 — the frame axis is bound to the header, and a shift rolls a segment *(RC-2)*

`app/bin_codec/scan.py` — `AXIS_INDEX` / `AXIS_STOCK` / `AXIS_INDEX_FNO` and
`read_header_axis(path, kind)`, cheap enough to call on every writer open. Also fixed
`scan_frames` to *prove* a frame's payload is present (`seek(...) > file_size`) rather than
trusting a seek past EOF, which had been counting a torn frame as good.

`app/bin_codec/writer.py` — `HeaderAxisMismatch`, `MAX_SEGMENTS = 24`, and
`resolve_segment_path(path, axis_len, kind)` which returns the first `<date>.pN.bin` that is
empty or axis-compatible. `_BaseWriter` gained `_axis_kind`, `_axis_on_disk`, `_commit_axis`,
`bind_header`, `flush_header`; `write_header` is now `bind + flush`. All three writers take
their frame width from **whichever header the file is committed to**, never blindly from the
incoming one.

`app/capture/engine.py` — `build_index_writer` / `build_stock_writer` /
`build_index_fno_writer` resolve the segment first and log the roll.

Documented in `docs/20-data-and-storage/bin-structure-spec.md` §2.1.

### P0-4 — AUTH_BLOCKED reaches `/health`, and one alert replaces 98 *(RC-3)*

`app/session_service.py` — `AUTH_OK` / `AUTH_NO_SESSION` / `AUTH_BLOCKED`, `_set_auth_state`
(returns `True` only on a *change*, so transitions are logged once), `auth_state`,
`auth_blocked_seconds()`. `_acquire_broker_session_unlocked` now separates *the broker has
nothing yet* (a wait) from *the broker's token was refused by Kite* (an administrator action
item, logged at `error` and re-raised). `status()` exposes `auth_state`,
`auth_blocked_seconds`, `auth_last_error`, `auth_failures`.

`app/api/health.py` — a fourth component, `broker_session`. The important check is not the
403 itself but the obligation: **market session OPEN and capture not running** → `stale`,
escalating to `dead` + HTTP 503 after `NOT_CAPTURING_DEAD_AFTER_S = 120` (immediately when
`AUTH_BLOCKED`). This is the only check that can notice a whole missing session, which every
loss percentage structurally cannot.

`app/ops/automation.py` — `_log_action_failure` suppresses repeats and re-reports a
*persisting* failure every `FAILURE_SUMMARY_INTERVAL_S = 300` with its duration and attempt
count, instead of an identical `WARNING` per poll.

### P1-1 / P1-2 / P1-4 — a middle recovery rung, evidence-based arming, honest telemetry

* **P1-1** `CaptureEngine` gained `reconnect_after_ms` / `reconnect_limit_per_hour`,
  `set_reconnect_fn`, `_reconnect_budget_left` (rolling hour) and `_try_reconnect`, tried in
  `observe_feed_health` *before* escalation. It deliberately does **not** reset the stale
  spell, so a reconnect that fails still reaches the restart deadline. Config:
  `CAPTURE_RECONNECT_AFTER_SECONDS = 10`, `CAPTURE_RECONNECT_MAX_PER_HOUR = 6`.
  `TickerBridge.reconnect()` — previously dead code — is now wired from bootstrap and from
  `engine.run`.
* **P1-2** `recovery_armed` returns `True` when the time-based session gate passes **or** the
  feed has already delivered a tick inside the session. Evidence beats elapsed time: the
  300 s arm delay is what turned a detectable ~47 s fault into a ~295 s hole.
* **P1-4** `_persist_telemetry_now()` is called in `_escalate_stale` before it raises and in
  `run()`'s `finally`. `_apply_carried` now restores with `max()` rather than assignment,
  because the throttled snapshot is always a *lower bound*.

### P2-1 / P2-2 — archive truth, and buckets that are counted once

* **New** `app/ops/reconcile_session.py` — `segment_paths`, `artifact_timestamps`,
  `reconcile_artifact`, `reconcile_session`. Wires up `ops/completeness.py`, which had no
  caller. The session figure is the **worst** artifact, not the pooled average. Run from
  `automation._reconcile_session_from_archive` at EOD **before** compression, since
  `scan_frames` reads raw `.bin`.
* `app/ops/sessions.py` — `scheduled_windows_on()` on both `MarketSession` and
  `SessionRegistry`, giving the owed grid as absolute milliseconds.
* `app/ops/stats_store.py` — `record_reconciliation` / `load_reconciliation_history` in a
  separate log, deliberately, so the archive figure and the telemetry figure can be compared.
* `app/capture/monitor.py` — `_loss_breakdown` is now an ordered, single-counted cascade
  (`awaiting_first_tick` → `stale_feed` → `write_path` → `restart_downtime` →
  `not_running`), each clamped by what remains. **Restart downtime is no longer added to
  `grid_seconds_lost`**, which is what made the dashboard print "write fail 147s" for a
  session in which no write ever failed.
* Frontend: `MonitorKpis` now takes its value *and* its severity from the same
  schedule-relative metric ("Loss vs schedule"); `SessionHistory`'s "Data loss" column is
  renamed and rebound to `scheduled_loss_pct`; `DataLossDiagnostics` tiles are
  denominator-explicit ("Gap loss" vs "Loss vs observed — cannot see downtime") and the
  cause list gained "no feed yet".

### P2-3 — remaining accounting gaps

* `resume_from_disk` includes `index_fno_writer` (it was omitted), records
  `resume_frame_spread` and warns when artifacts diverge, and no longer subtracts a second
  from the downtime (`run()` resyncs to the *next* boundary, so the current second is lost
  too).
* `record_session_summary` **merges** generations — cumulative counters by `max`, day-level
  by newest, plus a `generations` count — instead of replacing the row. That is why the
  dashboard's ticks/sec ranged 28.7–927.3 with no physical meaning.
* `compress_file` refuses to replace an archive that decompresses to *more* bytes than the
  raw file holds (new `decompressed_size`), closing the post-EOD-restart overwrite hazard.
* `EXPECTED_FRAMES_PER_SESSION` is now actually effective, a *disabled* session yields a
  baseline of 0 rather than 100% phantom loss, and an empty `MARKET_HOLIDAYS` logs a warning
  at bootstrap naming the consequence.

### Tests added

| File | Tests | Covers |
|---|---:|---|
| `tests/test_ticker_connect_guard.py` | 8 | RC-1: a fake whose `connect()` returns normally and never fires `on_connect` — the exact production shape a well-behaved fake could not express |
| `tests/test_header_axis_integrity.py` | 8 | RC-2: mismatch refused, segment rolling, axis reuse, three-generation replay, no header without data |
| `tests/test_recovery_and_auth_alarms.py` | 17 | pre-first-tick suppression, evidence-based arming, the reconnect rung and its rate limit, `AUTH_BLOCKED` → degraded → dead+503 |
| `tests/test_archive_reconciliation.py` | 13 | late start, early stop, interior holes, segment union, worst-artifact selection, the 2026-08-28 replay, session-summary merging |

Updated because behaviour intentionally changed:
`test_stale_write_suppression_e2e.py` (a tickless session now writes **nothing**, not 2–5
zero-filled frames), `test_data_loss_telemetry.py` (rigs seed a first tick),
`test_restart_resume.py` (restart downtime has its own counter; carried counters use `max`),
`test_capture.py`, `test_writer_health.py`.

### Expected effect once deployed

Against the nine-session baseline in §23.2 (6,246 missing seconds, 3.005%):

| Cause | Seconds | Addressed by | Expected after |
|---|---:|---|---|
| Late start — broker token refused | 3,239 | P0-4 alarms it; cannot prevent an upstream outage | unchanged in size, but paged within ~2 min instead of unnoticed for 54 min |
| Dead first ticker | 1,817 | P0-1 (cause) + P0-2 + P1-2 | → ~0 |
| Mid-day stale → restart | 1,135 | P1-1 (≈2 s replacement instead of ≈47 s restart) | → ~50–150 |
| Bootstrap head loss | 55 | operator `.env`: `CAPTURE_START_TIME=09:10` | → ~0 |

Excluding an upstream auth outage, that projects **~1.45% → ~0.1%**. The unquantifiable win
is RC-2: the archive becomes correctly strike-indexed, which no percentage was ever
reporting.

### Verification commands

```bash
cd backend && .venv/bin/python -m pytest -q          # 619 passed
cd backend && .venv/bin/python -m ruff check app tests
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```
