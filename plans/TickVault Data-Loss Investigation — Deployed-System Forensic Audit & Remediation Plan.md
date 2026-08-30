# TickVault Data-Loss Investigation — Deployed-System Forensic Audit & Remediation Plan

## Mission

Perform a code-accurate and runtime-accurate investigation into the apparent market-data loss occurring in **TickVault / `market_data_dwndr`**.

Recent trading sessions have been reporting approximately:

- **3–5% apparent data loss** on multiple sessions;
- approximately **15% apparent data loss** on the previous trading session.

The objective is NOT to immediately patch the system.

First determine exactly:

1. what the monitoring system means by "data loss";
2. whether the reported percentages represent genuine missing market data;
3. whether part of the percentage is intentionally-created gaps;
4. whether the denominator/expected-frame calculation is wrong;
5. where genuine missing data enters the pipeline;
6. why the previous session was substantially worse;
7. which fixes should be implemented and in what priority.

Produce a detailed investigation and remediation document and save it in the existing:

`plans/`

directory.

Do not implement the fixes as part of this task unless separately instructed.

---

# Critical Architecture Context

TickVault is NOT a trading engine.

It is a self-hosted Zerodha Kite market-data recorder.

Its main production responsibility is:

`Kite market feed`
→ `live in-memory market state`
→ `1 Hz snapshots`
→ `.bin` files
→ `zstd archive`
→ verified archive storage

It records four major datasets:

1. Index option chains
2. F&O stock board
3. Consolidated index F&O
4. Historical candles

The live datasets are intended to be written on a **1-second grid**.

The capture system currently uses:

- one Kite WebSocket connection;
- one `TickerBridge`;
- one capture engine;
- per-output-file writer threads;
- a capture monitor;
- broadcaster/telemetry infrastructure.

The production FastAPI lifespan owns the capture lifecycle.

There are intentionally no normal browser-facing start/stop capture endpoints.

---

# Important Existing Behavior: Not Every Hole Is Automatically a Bug

The current recovery design deliberately suppresses writes when the feed is stale.

Current default behavior is approximately:

- 5 seconds without content-changing ticks:
  feed becomes stale and writes are suppressed;

- 60 seconds continuous staleness:
  process exits so Docker can restart it;

- 15 seconds sustained fresh data:
  required before clearing a stale spell;

- 300 second grace period after market open;

- restart/resume downtime:
  approximately 25–30 seconds according to existing tests.

A stale period therefore intentionally produces a **hole** in the recorded 1 Hz timeline instead of repeating a frozen frame.

This is a deliberate integrity policy.

The investigation MUST distinguish:

### Intentional honest holes

Frames not written because the system correctly detected stale market state.

from:

### Actual data loss

Data that should have been captured but was lost because of:

- WebSocket problems;
- subscription failures;
- reconnect problems;
- processing/backpressure;
- writer failures;
- persistence failures;
- process restarts;
- resource saturation;
- accounting bugs;
- session scheduling bugs;
- or another defect.

Do not treat all missing seconds as equivalent.

---

# Runtime Environment

## Monitoring UI

The deployed monitoring system can be inspected at:

`http://tickvault.beonedge.internal/monitor`

The hostname works through the existing Tailscale/internal-network + hosts-file configuration.

Use **Playwright with the browser view/GUI enabled** and inspect the actual deployed dashboard.

Do not rely only on static source-code inspection.

Today is Sunday, so there is no active Indian market capture session.

That is expected.

Use:

- historical session telemetry;
- persisted stats;
- previous capture history;
- alerts;
- completeness metrics;
- connection-quality history;
- frame counts;
- data-loss diagnostics;
- archives;
- logs;
- and runtime state

to investigate previous trading sessions.

---

# VPS

Connect using:

`ssh beonedge`

The deployed application bundle is located under:

`/srv/dev_stack/DATA_DOWNLOADER`

Important:

Do not automatically assume the VPS folder is a normal source-code Git checkout.

The project release architecture builds and ships a self-contained deployment bundle containing the production images and deployment artifacts.

Determine what is actually present on the VPS before treating it as source.

Use the source repository for code tracing and the VPS deployment for runtime evidence.

The deployed runtime is the authority for determining what version/configuration was actually running during the affected sessions.

---

# Investigation Phase 1 — Understand the Loss Metric Before Diagnosing the Loss

This is the highest-priority requirement.

Trace the implementation responsible for the percentage shown as "data loss".

Look especially through:

`backend/app/ops/completeness.py`

`backend/app/capture/monitor*`

`backend/app/ops/stats*`

`backend/app/api/status*`

`backend/app/api/stats*`

`backend/app/static/`

and any frontend monitoring telemetry/hooks if the Next.js monitoring surface participates.

Trace the complete path:

`captured frames`
→ `capture counters`
→ `persisted monitor stats`
→ `completeness calculation`
→ `API payload`
→ `/monitor`
→ displayed percentage

Document the exact:

- source file;
- function;
- numerator;
- denominator;
- expected-frame calculation;
- actual-frame calculation;
- time range;
- market-session definition;
- scheduled-second definition;
- per-dataset calculation;
- aggregation formula;
- restart behavior;
- stale-period behavior.

Answer explicitly:

> What does "3–5% data loss" mathematically mean in the current implementation?

Do not continue treating the metric as authoritative until this is proven.

---

# Investigation Phase 2 — Session-Schedule Drift Is a Primary Suspect

There is already a known architectural drift in the repository.

The currently deployed capture artifacts still fundamentally share a single:

`09:00–15:30`

session window.

However, the actual market closing/session structure changed on:

`2026-08-03`

and the repository documentation states that **no segment now cleanly matches that single shared window**.

There is already a newer session-registry implementation in:

`backend/app/ops/sessions.py`

with:

- per-session schedules;
- pre-open handling;
- scheduled-second accounting.

But not every artifact has migrated to it.

This is extremely relevant to apparent data loss.

Investigate whether the reported 3–5% or 15% can be partially or substantially explained by the monitoring system expecting frames during seconds when a particular market segment was not actually supposed to produce data.

Check whether:

`expected_seconds`

is effectively:

`09:00 → 15:30`

for every artifact regardless of the actual dataset/session.

Determine whether:

- index options;
- F&O stocks;
- index futures;
- BSE derivatives;
- NSE derivatives;

are being judged against inappropriate shared session boundaries.

Calculate the effect quantitatively.

For affected sessions determine:

`reported loss`

versus:

`loss after correct scheduled-second accounting`

This distinction is mandatory.

---

# Investigation Phase 3 — Reconstruct the Exact Capture Pipeline

Trace the current production path from Zerodha to disk.

Start from:

`backend/app/capture/bootstrap.py`

Then trace:

`instrument dumps`
→ `subscription planner`
→ `TickerBridge`
→ `Kite WebSocket`
→ `tick decoding`
→ `CaptureEngine`
→ `live matrices/tables`
→ `1 Hz snapshot`
→ `writer queues/threads`
→ `.bin`
→ monitoring counters

Cover all three main live domains:

### Index option chains

ATM ± 50 strikes:

101 strikes × call/put

plus spot and India VIX.

### F&O stock board

Spot + three nearest futures, with L5 market depth.

### Consolidated index F&O

Index spot + three nearest futures.

Document the exact source files/functions responsible for each stage.

Create an architecture/data-flow diagram in the final report.

---

# Investigation Phase 4 — Determine Where Missing Frames Originate

For every detected gap, attempt to classify it.

Use categories such as:

### A — Feed never delivered usable data

Possible causes:

- Kite connection interruption;
- provider-side outage;
- network interruption;
- token/session problem.

### B — Feed was connected but subscription coverage was incomplete

Possible causes:

- subscription planner issue;
- subscription limits;
- failed subscription batch;
- reconnect without complete resubscription;
- instrument-universe mismatch.

### C — Data arrived but CaptureEngine rejected/suppressed it

Including legitimate stale-write suppression.

### D — Data entered processing but was dropped

Possible causes:

- queue overflow;
- lagging consumer;
- thread starvation;
- failed channel send;
- race condition;
- lock contention.

### E — Snapshot was produced but writer failed

Possible causes:

- writer queue issue;
- storage error;
- filesystem stall;
- thread failure;
- serialization exception.

### F — Frame exists on disk but monitoring failed to count it

This is an accounting/telemetry problem, not data loss.

### G — Frame is being incorrectly expected

This is a completeness/session-schedule calculation problem.

The report should assign evidence-backed gaps to these categories wherever possible.

---

# Investigation Phase 5 — WebSocket and Reconnect Forensics

The system uses one Kite WebSocket feed for the subscribed universe.

Inspect:

`backend/app/kite/`

and particularly the ticker bridge / reconnect path.

Determine exactly what happens when the connection drops.

Inspect:

- initial subscription planning;
- subscription batching;
- token count;
- connection-state tracking;
- disconnect detection;
- reconnect behavior;
- resubscription behavior;
- subscription-state reconstruction;
- stale detection;
- authentication handling;
- retry/backoff;
- connection exceptions;
- callbacks;
- thread/task lifecycle.

Important question:

> After a reconnect, is the entire authoritative subscription universe resubscribed, or can the system resume with only a subset?

Verify this from code and, if possible, from historical logs.

A partial resubscription could produce sustained data loss while leaving the WebSocket technically "connected".

Check for this explicitly.

---

# Investigation Phase 6 — Feed Freshness Logic

Inspect the exact implementation of the stale-feed detector.

The existing architecture considers:

"No content-changing ticks"

different from merely:

"No network packets."

Determine exactly what qualifies as a content-changing tick.

Investigate whether legitimate periods of unchanged prices can incorrectly trigger staleness.

Especially examine:

- thinly traded instruments;
- index feeds;
- early/pre-open periods;
- expiry instruments;
- inactive contracts;
- BSE vs NSE behavior.

Determine whether the freshness signal is based on:

- any subscribed instrument;
- each dataset individually;
- particular anchor instruments;
- total tick volume;
- changed values;
- timestamps.

This could produce false stale detection even if the Kite connection itself remains healthy.

---

# Investigation Phase 7 — Understand the 1 Hz Snapshot Semantics

TickVault does NOT attempt to store every individual tick.

It stores a 1 Hz snapshot of the market state it watches.

Therefore do not confuse:

`WebSocket tick count`

with:

`expected stored frame count`.

The primary persistence expectation is effectively:

approximately one valid snapshot per scheduled capture second per dataset/file,

subject to intentional stale suppression and applicable market-session rules.

Determine precisely:

- how the 1 Hz scheduler operates;
- whether it uses wall-clock seconds or elapsed intervals;
- whether execution drift accumulates;
- what happens if one snapshot takes >1 second;
- whether missed scheduling intervals are skipped or caught up;
- whether writer latency blocks snapshot generation;
- whether multiple files are synchronized to the same scheduled second.

Investigate timer/scheduler drift.

A system consistently running slightly slower than 1 Hz could naturally create several-percent frame deficits across a trading day.

Measure this rather than assuming the scheduler is precise.

---

# Investigation Phase 8 — Concurrency and Backpressure

Inspect:

`backend/app/capture/`

with particular attention to:

- writer threads;
- queues;
- channels;
- snapshot generation;
- broadcaster;
- capture monitoring.

For every queue/buffer determine:

- capacity;
- producer;
- consumer;
- blocking semantics;
- overflow behavior;
- failure behavior;
- timeout behavior.

Search for:

- ignored queue errors;
- silent send failures;
- exception swallowing;
- log-only write failures;
- queue-full conditions;
- dead threads;
- daemon thread behavior;
- mutex contention;
- excessive copies;
- long synchronous work inside async paths.

Determine whether slow storage can propagate backward into capture.

---

# Investigation Phase 9 — Binary Writer and Persistence Integrity

Inspect:

`backend/app/bin_codec/`

The binary files use:

`header_frame + data_frame*`

and append during the session.

A process restart should reopen the existing file, scan it, recover the existing frame count, and continue without creating another header.

Verify this behavior against actual affected files.

Check:

- frame sequence numbers;
- timestamps;
- gaps;
- duplicate timestamps;
- corrupt frames;
- torn trailing frames;
- writer reopen behavior;
- flush behavior;
- fsync behavior;
- partial writes.

Remember that a torn trailing frame is intentionally ignored by the reader.

Determine whether any affected `.bin` contains valid frames that the monitoring layer failed to count.

---

# Investigation Phase 10 — Compare Files Against Monitoring

For several recent sessions, independently inspect persisted `.bin` or `.bin.zst` data.

Do not trust dashboard numbers alone.

For each selected file calculate:

- first valid frame;
- last valid frame;
- number of frames;
- number of unique timestamps;
- duplicate timestamps;
- missing scheduled timestamps;
- longest gap;
- distribution of gaps;
- sequence-number gaps;
- torn/corrupt frame count if any.

Compare these independently-derived values against the monitoring statistics.

The objective is to distinguish:

`monitor says missing`

from:

`file actually missing`.

---

# Investigation Phase 11 — Analyze the ~15% Session Separately

Identify the exact trading date corresponding to the approximately 15% loss.

Treat it as a dedicated incident.

Compare it with at least:

1. one low-loss/reasonably healthy session;
2. one typical 3–5% session;
3. the ~15% session.

Build a timeline for the 15% session.

Include:

- session start;
- capture start;
- stale spells;
- reconnects;
- process restarts;
- Docker restarts;
- token/session events;
- writer failures;
- network events;
- EOD events;
- capture stop;
- archive/compression.

Determine whether the 15% loss consists of:

- one major outage;
- several medium outages;
- many small recurring gaps;
- late capture start;
- early capture termination;
- incorrect expected-session duration;
- one broken dataset;
- systemic loss across every dataset.

This distinction is crucial.

---

# Investigation Phase 12 — Use Persisted Monitor State

Operational state exists under:

`MARKET_DATA_PATH/_state/stats/`

including:

- persisted monitor snapshots;
- compression history;
- per-date escalation ledger.

Use this historical state.

Because counters persist across mid-session restarts, determine whether the statistics correctly survive restarts.

Check for:

- double counting;
- lost counters;
- reset counters;
- incorrectly restored counters;
- stale state;
- date rollover mistakes.

Correlate escalation entries with frame gaps.

---

# Investigation Phase 13 — Inspect Automation and Restart Recovery

Inspect:

`backend/app/ops/automation.py`

Production behavior is driven by the FastAPI lifespan and `DailyAutomationService`, polling approximately every 5 seconds.

Trace:

`decide_automation`
→ session readiness
→ capture start
→ capture stop
→ restart behavior
→ EOD

Determine whether a process restart during market hours causes unnecessary bootstrap delay.

The documented restart downtime is approximately:

25–30 seconds.

If there were multiple restarts during a session, calculate their expected contribution to the observed loss percentage.

For example:

`restart count × actual measured restart gap`

versus:

`total missing scheduled seconds`

Do not simply state that restarts cause loss; quantify their contribution.

---

# Investigation Phase 14 — Partial Bootstrap / Degraded Startup

Bootstrap deliberately degrades instead of aborting when a capture domain fails.

A failed:

- index discovery;
- stock-board discovery;
- index-F&O discovery;

can allow capture to continue as long as something remains capturable.

This creates another important possibility:

The service can report itself as running while part of the market universe is missing.

Inspect bootstrap logs and telemetry for affected sessions.

Determine:

- expected indices;
- discovered indices;
- skipped indices;
- expected stock board;
- discovered stocks;
- subscription universe;
- final actual subscribed token count.

The default configured index universe currently includes:

- NIFTY
- BANKNIFTY
- FINNIFTY
- MIDCPNIFTY
- SENSEX
- BANKEX

unless overridden through `INDICES`.

Check actual production configuration.

---

# Investigation Phase 15 — Subscription Coverage

Build the actual subscription accounting chain:

`instrument universe discovered`
→ `tokens planned`
→ `tokens requested`
→ `tokens subscribed`
→ `tokens producing ticks`
→ `tokens represented in snapshots`

Report numerical counts where the implementation/runtime makes them available.

Look for:

- missing tokens;
- duplicate tokens;
- expired contracts;
- stale instrument masters;
- subscription-limit issues;
- tokens that disappear after reconnect;
- incorrectly selected expiries.

The daily archived instrument masters under:

`_instruments/<date>/`

should be used where useful to reconstruct what the application believed the universe was on the affected date.

---

# Investigation Phase 16 — Runtime and Docker Audit

The deployment uses hardened backend/frontend containers.

Inspect actual production configuration using the VPS deployment bundle.

Check:

- container status;
- uptime;
- restart counts;
- health checks;
- restart policies;
- environment variables;
- mounted data paths;
- mounted archive paths;
- logs;
- container resource limits;
- memory constraints;
- file descriptor limits.

Correlate backend restart timestamps with file gaps.

Do not modify production configuration during this investigation.

---

# Investigation Phase 17 — VPS Resource Audit

Check whether infrastructure pressure contributed.

Investigate available evidence for:

- CPU saturation;
- RAM pressure;
- swap usage;
- OOM kills;
- disk usage;
- inode usage;
- disk latency;
- filesystem errors;
- I/O stalls;
- network errors;
- TCP retransmissions;
- socket errors;
- file descriptor exhaustion.

Relevant Linux/Docker historical logs should be inspected where available.

Do not assume an infrastructure issue without correlating its timestamp with capture gaps.

---

# Investigation Phase 18 — EOD Is Not the Same as Live Capture Loss

At EOD the system:

1. stops capture;
2. flushes writers;
3. fsyncs;
4. zstd-compresses raw files;
5. verifies compressed data byte-for-byte against the source;
6. atomically publishes the archive;
7. removes the raw file only after successful verification.

Therefore separately distinguish:

### Live frame completeness

from:

### EOD archive integrity.

A successfully verified archive proves that the archive accurately contains the raw captured file.

It does NOT prove that every expected market second was originally captured.

Do not use successful compression verification as evidence that there was no live-capture loss.

---

# Investigation Phase 19 — Frontend vs Backend Monitoring

There are potentially two monitoring representations in the project:

- FastAPI's dependency-free `/monitor` dashboard;
- Next.js monitoring functionality/hooks.

Determine exactly which implementation is serving:

`http://tickvault.beonedge.internal/monitor`

in the deployed stack.

Trace the values shown there back to the backend.

Do not spend substantial effort redesigning monitoring UI.

The UI is evidence in this task, not the target of the task.

However, if the UI itself calculates or transforms loss percentages incorrectly, document it as a defect.

---

# Investigation Phase 20 — Existing Tests

The backend already contains substantial behavioral coverage.

Relevant tests include areas such as:

- restart/resume;
- stale-write suppression;
- daily automation;
- session lifecycle.

Read those tests because they encode intended behavior.

But do not assume passing tests prove production behavior.

The real Kite WebSocket and REST paths are not covered by CI using live credentials.

Identify gaps between:

`tested synthetic behavior`

and:

`real provider behavior`.

Recommend additional tests specifically targeting whatever caused the production loss.

---

# Investigation Phase 21 — Do Not Confuse Historical Downloader Code

`backend/app/historical/`

is implemented and tested but currently has no active caller from:

- route;
- CLI;
- scheduler;
- frontend.

It is currently effectively an unreachable library.

Do not attribute the current live-session loss to historical-download workers unless you discover runtime evidence contradicting this architecture.

The investigation is primarily about the live capture system.

---

# Required Root-Cause Classification

Every identified issue should be classified into one of these levels:

## CONFIRMED

Direct evidence proves it contributed to loss.

## HIGHLY LIKELY

Strong code/runtime correlation exists but production evidence is incomplete.

## POSSIBLE

A valid failure path exists but there is currently no evidence it occurred.

## RULED OUT

Investigation demonstrates that it did not materially contribute.

Do not present speculative failure modes as confirmed root causes.

---

# Required Quantitative Breakdown

For each investigated session, ideally provide something like:

| Component | Expected Seconds | Written Frames | Missing | Missing % | Cause |
|---|---:|---:|---:|---:|---|
| NIFTY | ... | ... | ... | ... | ... |
| BANKNIFTY | ... | ... | ... | ... | ... |
| FINNIFTY | ... | ... | ... | ... | ... |
| MIDCPNIFTY | ... | ... | ... | ... | ... |
| SENSEX | ... | ... | ... | ... | ... |
| BANKEX | ... | ... | ... | ... | ... |
| STOCKS | ... | ... | ... | ... | ... |
| INDEX_FNO | ... | ... | ... | ... | ... |

Where possible additionally decompose missing seconds into:

`incorrect session expectation`
+
`intentional stale suppression`
+
`restart downtime`
+
`late startup`
+
`early shutdown`
+
`subscription failure`
+
`writer/persistence loss`
+
`unexplained`

The final unexplained portion is especially important.

---

# Required Gap Distribution Analysis

Do not report only a single percentage.

Analyze the topology of the missing data.

For each affected session determine:

- total gaps;
- average gap duration;
- median gap duration;
- maximum gap duration;
- number of 1-second gaps;
- number of 2–5 second gaps;
- number of >5 second gaps;
- number of >30 second gaps;
- number of >60 second gaps.

This helps distinguish:

- scheduler jitter;
- stale suppression;
- restart downtime;
- long WebSocket outages;
- systematic session-boundary errors.

---

# Remediation Design Requirements

After determining the causes, propose fixes in priority order.

Use:

## P0 — data correctness / active loss

Issues that currently destroy or fail to capture valid market data.

## P1 — resilience

Reconnect, resubscription, restart, queue, writer, monitoring reliability.

## P2 — accounting correctness

Expected-frame/session calculations and false loss reporting.

## P3 — observability

Metrics necessary to identify the next failure immediately.

For every proposed fix provide:

- root cause addressed;
- exact files/modules likely affected;
- proposed behavior;
- failure mode prevented;
- tests required;
- operational verification;
- deployment risk.

---

# Observability Improvements

Regardless of the eventual root cause, determine whether TickVault currently exposes enough information to diagnose future gaps.

Recommend metrics where missing, particularly:

- expected scheduled second;
- snapshot sequence number;
- frame written sequence number;
- last tick timestamp;
- last content-changing tick timestamp;
- last snapshot timestamp;
- WebSocket connection epoch;
- reconnect count;
- subscription count;
- post-reconnect subscription count;
- tick count per second;
- writer queue depth;
- writer lag;
- stale-suppressed seconds;
- restart-gap seconds;
- late-start seconds;
- session-schedule-excluded seconds.

The goal is that future "5% loss" reports can immediately say **where the 5% came from**.

---

# Safety / Production Rules

This investigation is against a deployed production-like capture system.

Do NOT:

- manually start capture today;
- fabricate market traffic against production;
- alter production `.env`;
- delete data;
- rewrite `.bin` files;
- recompress archives;
- modify deployed containers;
- restart services merely for testing;
- change Docker configuration;
- change Tailscale/network configuration.

Read-only inspection is the default.

If an experiment would mutate production state, document the experiment instead of executing it.

---

# Source-of-Truth Hierarchy

When documentation and implementation disagree, use this priority:

1. actual deployed runtime behavior/configuration;
2. current source code;
3. current behavioral tests;
4. as-built repository documentation;
5. older design/specification documents.

The repository already contains known documentation drift, so do not assume every existing document describes the deployed behavior correctly.

---

# Deliverable

Create a detailed Markdown document in:

`plans/`

Use an appropriate name such as:

`plans/tickvault-data-loss-root-cause-and-remediation-plan.md`

The document should contain at minimum:

1. Executive Summary
2. Incident Description
3. Meaning of the Current "Data Loss" Metric
4. Deployed Architecture
5. Live Capture Data Flow
6. Session Scheduling / Expected-Second Analysis
7. Historical Session Comparison
8. ~15% Loss Session Incident Timeline
9. Gap Distribution Analysis
10. WebSocket / Subscription Findings
11. Feed Freshness / Stale-Suppression Findings
12. 1 Hz Scheduler Findings
13. Restart / Recovery Findings
14. Concurrency / Backpressure Findings
15. Binary Persistence Findings
16. Monitoring / Accounting Findings
17. Docker / VPS Findings
18. Confirmed Root Causes
19. Highly Likely Contributors
20. Possible but Unconfirmed Failure Modes
21. Ruled-Out Causes
22. Quantitative Loss Attribution
23. Proposed P0/P1/P2/P3 Fixes
24. Exact Files Expected to Change
25. Tests Required
26. Production Verification Plan
27. Observability Improvements
28. Remaining Unknowns

---

# Final Requirement

Do not finish with a generic list of ways market-data systems can lose data.

I want this report based specifically on **this TickVault implementation and this deployed system**.

Every important conclusion must point back to evidence such as:

- source files/functions;
- deployed configuration;
- logs;
- persisted statistics;
- actual binary-frame analysis;
- Docker state;
- browser-observed telemetry;
- or existing tests.

Most importantly, answer these questions:

1. **Is the reported 3–5% actually 3–5% genuine market-data loss?**
2. **How much of it is caused by wrong expected-session accounting?**
3. **How much is deliberate stale-write suppression?**
4. **How much is process/reconnect downtime?**
5. **Is any data being lost while the feed is otherwise healthy?**
6. **What specifically caused the approximately 15% session?**
7. **What exact changes will reduce genuine unexplained loss as close to zero as practically possible?**

Stop after producing the investigation/remediation document.

Do not implement the fixes yet.