TICKVAULT LIVE DATA PIPELINE — ARCHITECTURAL EVOLUTION PLAN

IMPORTANT IMPLEMENTATION CONSTRAINT

This is an existing and functioning TickVault codebase.

Do NOT redesign the repository from scratch.
Do NOT impose a new folder structure.
Do NOT assume filenames, modules, classes, services, or interfaces without first inspecting the existing implementation.
Do NOT refactor unrelated parts of the backend or frontend simply to fit this plan.

First analyze the current codebase and determine how the existing abstractions, configuration system, capture controller, WebSocket ingestion, state handling, binary writers, telemetry, health monitoring, and frontend status reporting already work.

Then implement the following architectural changes by extending the existing design with the smallest reasonable set of modifications.

The backend is Python.
The frontend is TypeScript with Next.js.

The objective is architectural evolution, not architectural replacement.


1. PRESERVE THE EXISTING DATA DOMAINS

The existing capture system currently persists:

- NIFTY50 in its own dedicated binary dataset
- BANKNIFTY in its own dedicated binary dataset
- FINNIFTY in its own dedicated binary dataset
- SENSEX in its own dedicated binary dataset
- Stock F&O data, including market depth, in one consolidated binary dataset

These existing storage formats and historical datasets should not be modified unnecessarily.

A new requirement is being introduced:

Capture F&O data for all six supported indices.

Treat index F&O as a new logical data domain rather than forcing it into the existing stock F&O binary representation.

The preferred architectural direction is therefore:

existing index datasets
+
existing stock F&O dataset
+
one additional consolidated index F&O dataset

The actual implementation, naming, schema integration, writer abstraction, and code placement must be determined after analysing the existing codebase.

Do not create six separate index-F&O datasets unless analysis of the existing binary format or runtime requirements provides a strong technical reason to do so.

A consolidated index-F&O dataset is preferred because the intended use includes cross-index and derivatives arbitrage research, where synchronized access to multiple index derivative instruments is useful.


2. DO NOT CHANGE THE EXISTING STOCK F&O FORMAT JUST TO SUPPORT INDEX F&O

Stock F&O and index F&O should remain logically independent capture domains.

Index F&O should not be added to the stock F&O binary representation merely because both are derivatives.

The reasons are:

- preserve existing binary compatibility
- preserve existing historical stock data
- prevent unnecessary migration work
- keep stock and index derivative schemas independently evolvable
- isolate corruption or writer failures
- simplify arbitrage-oriented reads
- allow different universe-selection rules in the future
- allow different depth requirements if needed later

Reuse existing writer infrastructure wherever possible, but do not modify the stock F&O binary contract unless analysis finds a requirement unrelated to this new index-F&O feature.


3. INTRODUCE THE CONCEPT OF MARKET SESSIONS

The current system should evolve away from treating one global capture start time and one global capture end time as universally applicable to every captured artifact.

Different captured datasets may eventually belong to different market sessions.

Introduce a conceptual Market Session abstraction.

A market session should represent things such as:

- session timezone
- bootstrap period
- pre-open period where applicable
- normal trading start
- normal trading end
- whether capture is expected during a particular phase
- whether stale detection should be armed
- whether recovery escalation should be allowed

Do not make every individual artifact carry duplicated hardcoded trading times.

Instead, artifacts should logically reference the market session they belong to.

The actual code representation must follow existing repository patterns.


4. KEEP MARKET TIMINGS CONFIGURABLE

Existing behaviour allows capture timings to be seeded through environment configuration.

Preserve that capability.

Do not hardcode exchange timings throughout business logic.

However, evolve configuration from the idea of:

one global opening time
one global closing time

towards:

session-oriented timing configuration

The system should be capable of representing multiple session types without requiring a separate environment variable for every individual binary writer.

For example, multiple artifacts belonging to the same derivatives session should consume the same derivatives-session configuration.

Do not create configuration such as:

NIFTY_CLOSE
BANKNIFTY_CLOSE
FINNIFTY_CLOSE
SENSEX_CLOSE
STOCK_FNO_CLOSE
INDEX_FNO_CLOSE

when these artifacts share the same market session.

Configuration should express market/session behaviour, while application logic maps artifacts to those sessions.


5. SEPARATE PROCESS STARTUP FROM MARKET CAPTURE

The backend process being alive must not mean that every writer is currently supposed to persist market frames.

Conceptually separate:

process startup
bootstrap
market-data connection
subscription preparation
pre-open
continuous trading
capture activation
capture deactivation
writer finalization
end-of-day work

The backend may start before normal trading begins.

During this period it can:

- load instrument masters
- resolve contracts
- build token mappings
- construct live state
- initialize writers
- initialize telemetry
- establish the broker connection
- prepare subscriptions
- perform required reference-data initialization

Actual persistence should depend on the active session of the corresponding artifact.


6. REPRESENT ARTIFACT LIFECYCLE INDEPENDENTLY

Each logical captured artifact should conceptually have its own lifecycle state.

Examples of relevant state:

INACTIVE
PREOPEN
ACTIVE
CLOSING
CLOSED

Exact enum names are not important.

The important architectural property is that the system can determine:

Is this artifact currently expected to receive data?

Is this artifact currently expected to create 1 Hz frames?

Should stale data be considered abnormal for this artifact right now?

Should recovery escalation be armed for this artifact/session?

Should its writer currently accept frames?

This prevents global assumptions from producing false stale alerts or incorrect loss statistics.


7. PRESERVE THE 1 HZ CAPTURE MODEL

Do not change the fundamental capture strategy.

Ticks should continue to be ingested and applied continuously.

Persistence should continue to sample the latest state at the deterministic 1 Hz grid.

The architecture remains:

continuous tick ingestion
->
continuous live-state updates
->
1 Hz snapshot
->
binary persistence

Do not convert the archive into tick-by-tick storage.

The predictable 1 Hz state archive is intentional and should remain.


8. PRESERVE RAW-ONLY STORAGE

Do not introduce derived analytics into the capture format.

Continue storing the minimum raw market information necessary to reconstruct analytics later.

Do not persist derived values merely because the new index-F&O dataset will be used for arbitrage.

Examples that should remain reconstruction-time calculations include:

- Greeks
- IV
- spreads
- futures basis
- synthetic futures
- arbitrage signals
- change calculations
- strategy signals
- calculated relationships between indices

Capture raw market state.

Perform analytics during reading, replay, backtesting, or analytics processing.

This ensures historical market data remains reusable when calculation logic changes.


9. MAKE INDEX F&O SUITABLE FOR CROSS-INSTRUMENT ANALYSIS

The new index-F&O dataset should be designed with synchronized cross-index analysis in mind.

The six supported indices should be captured on the same 1 Hz timing grid.

The objective is that one logical frame represents the latest-known state of the relevant derivative universe for that timestamp.

This is valuable for:

- index futures basis analysis
- calendar-spread analysis
- cross-index spread analysis
- relative-value analysis
- synthetic instrument calculations
- arbitrage research
- lead/lag analysis
- later strategy replay

Do not optimize the representation around one arbitrage strategy.

Store sufficiently general raw state so multiple strategies can be reconstructed from the same archive.


10. ANALYSE THE SUBSCRIPTION UNIVERSE BEFORE ADDING INDEX F&O

The current subscription count should be recalculated once six-index F&O capture is added.

Do not rely on the previous token estimate.

At bootstrap, determine the complete subscription requirement dynamically.

The calculation should include all currently required:

- index instruments
- stock instruments
- stock derivatives
- index futures
- index options
- spot/reference instruments
- volatility/reference instruments
- any other tokens already consumed by TickVault

The system should know:

actual subscribed token count
broker connection capacity
remaining capacity
utilization percentage

Introduce a configurable safety margin below the broker's absolute connection limit.

If the universe remains comfortably below the safe threshold, keep one WebSocket connection.

Do not introduce multiple connections unnecessarily.

If the universe exceeds the safe operating threshold, shard subscriptions using the existing architecture in the least invasive way possible.

The subscription planner should make that decision based on actual runtime requirements rather than static assumptions.


11. IMPROVE FEED FRESHNESS SEMANTICS

The current stale detection should evolve from a single concept of freshness into multiple signals.

Three distinct conditions should be observable:

A. Transport freshness

Question:

Are broker packets actually arriving?

Maintain a monotonic timestamp representing the most recent received packet.

If packets stop arriving entirely, this is a transport-level stale condition.


B. Artifact freshness

Question:

Is a particular logical data domain receiving relevant updates?

For example:

The WebSocket may still be healthy while index derivatives stop updating.

That must be distinguishable from the complete broker feed dying.


C. Content activity

Question:

Are incoming values actually changing?

The existing content-digest mechanism remains useful.

However, unchanged market content should not automatically mean the transport itself is disconnected.

It may indicate a quiet market or an artifact-level problem.


12. INTRODUCE CLEAR HEALTH SEMANTICS

The system should be capable of distinguishing conditions conceptually equivalent to:

HEALTHY

Packets are arriving and expected artifacts are updating.

QUIET

Packets are arriving but relevant market values have not materially changed.

ARTIFACT_STALE

The WebSocket is alive but one or more expected artifacts are not receiving relevant updates.

TRANSPORT_STALE

Broker packets themselves are not arriving.

RECOVERY_PENDING

A failure condition has exceeded the normal stale threshold and restart/recovery escalation is pending.

RECOVERY_ABANDONED

The configured restart budget has been exhausted.

Exact names should follow existing project conventions.


13. KEEP STALE-WRITE SUPPRESSION

Do not return to writing duplicated last-known market frames during genuine stale conditions.

If a frame cannot be considered valid because its feed is stale, persistence should continue to suppress that frame.

The dashboard may continue displaying the last-known state with a stale indication.

The archive should contain an observable timestamp/sequence gap rather than silently duplicating old values.

Missing data is auditable.

Duplicated stale data masquerading as real data is not.


14. MAKE STALE DETECTION SESSION-AWARE

A data source must not be declared stale simply because an artifact is outside the period where updates are expected.

Before evaluating stale escalation, the system should determine:

Is this artifact currently expected to receive data according to its market session?

If no:

do not count inactivity as data loss
do not trigger stale escalation
do not restart the process because of that artifact

If yes:

apply normal freshness and recovery logic.

This is particularly important around:

- startup
- pre-open
- normal market open
- market close
- holidays
- intentionally disabled capture windows


15. PRESERVE RESTART-FIRST RECOVERY

Keep the current restart-first recovery philosophy.

Do not reintroduce complex destructive in-process token-refresh/reconnect ladders unless separately proven necessary.

The expected failure model remains conceptually:

feed becomes stale
->
stale writes suppressed
->
stale condition persists beyond deadline
->
writers drain safely
->
process exits
->
container restarts cleanly
->
bootstrap repeats
->
writer resumes from persisted state

Preserve the existing daily restart budget so a persistent external failure cannot cause endless restart thrashing.


16. MAKE RECOVERY DEPEND ON ACTIVE MARKET EXPECTATIONS

Recovery escalation should not be based simply on:

"time since content changed"

It should roughly reason as follows:

Is at least one artifact currently in a session where broker updates are expected?

If no:
do not escalate.

If yes:
check transport and artifact health.

If the complete transport is stale beyond the configured threshold:
use restart-first recovery.

If only one artifact is stale while the transport remains healthy:
record and expose the artifact-level condition before deciding whether whole-process restart is justified.

The implementation should avoid turning every localized data anomaly into an immediate process restart.


17. MAKE DATA-LOSS ACCOUNTING SESSION-AWARE AND INCLUDE APPLICATION/SERVER DOWNTIME

TickVault's data-loss calculation must account for every second during which market data was expected but valid data was not persisted.

This includes not only stale-feed conditions and writer failures, but also periods where the TickVault capture application or the underlying server was not running during an active market session.

The fundamental rule should be:

If an artifact was scheduled to capture market data at a particular second, but no valid frame exists for that second, that second represents data loss regardless of whether the cause was:

* broker/feed staleness
* network outage
* application crash
* container restart
* application being manually stopped
* server shutdown
* server reboot
* operating-system failure
* power failure
* writer failure
* startup/recovery delay
* any other interruption of the capture system

Time outside the configured capture session must not count as data loss.

17.1 DEFINE EXPECTED MARKET TIME INDEPENDENTLY OF PROCESS UPTIME

The expected capture timeline must not depend on the TickVault process actually being alive.

This is essential.

If the application is offline for ten minutes, it obviously cannot increment an in-memory grid counter during those ten minutes.

Therefore the system must be able to reconstruct the expected capture grid from:

* trading date
* configured market/session schedule
* artifact/session membership
* configured capture cadence
* actual wall-clock timestamps
* applicable market-calendar rules

For a 1 Hz artifact, every scheduled market second represents one expected frame.

Conceptually:

# EXPECTED FRAMES

number of scheduled capture seconds

The expected frame count must be calculable even if TickVault was completely offline for part of the session.

17.2 DEFINE TOTAL DATA LOSS AS MISSING EXPECTED MARKET SECONDS

Conceptually:

# total_missing_seconds

stale_feed_loss
+
application_or_server_downtime_loss
+
write_path_loss
+
other_missing_capture_seconds

And:

# data_loss_pct

total_missing_seconds
/
scheduled_capture_seconds
× 100

This should represent the honest completeness of the historical dataset.

17.3 TRACK DIFFERENT CAUSES OF LOSS WHERE POSSIBLE

Total data loss is the primary metric.

However, TickVault should attempt to classify the cause of missing data when sufficient information exists.

Useful conceptual categories are:

stale_feed_loss

The application was running and the relevant market session was active, but the incoming feed was stale and persistence was intentionally suppressed.

process_downtime_loss

The TickVault capture application was not running during a period where an artifact was scheduled to capture data.

server_downtime_loss

If sufficient external or persisted information exists to distinguish this from an application-only outage, the entire server was unavailable during scheduled capture time.

write_path_loss

The process was alive, a valid frame existed, but persistence failed.

unclassified_loss

A scheduled frame is absent, but the system cannot confidently determine why.

Do not sacrifice correctness merely to force every missing second into a specific category.

If the cause cannot be proven, classify it as unknown/unclassified loss while still including it in total data loss.

17.4 THE BINARY DATA ITSELF SHOULD REMAIN THE PRIMARY EVIDENCE OF COMPLETENESS

Telemetry is useful for explaining why data is missing.

It should not be required to determine whether data is missing.

A restart, crash, server shutdown, or filesystem issue can also interrupt telemetry.

Therefore historical completeness should primarily be determined from:

expected session timeline
versus
persisted frame timestamps/sequence information

Telemetry should then provide additional attribution such as:

feed stale
writer failed
process restarted
server/application unavailable

This means that even if TickVault disappears unexpectedly for twenty minutes and no telemetry is written during that period, the next startup should still be capable of determining:

twenty scheduled minutes passed
no persisted frames exist
therefore those twenty minutes are data loss.

17.5 RECONSTRUCT DOWNTIME AFTER RESTART

When TickVault starts or restarts during an active market session, compare the last successfully persisted market timestamp against the current expected session grid.

Example:

last valid persisted frame:
11:20:14

TickVault becomes operational again:
11:27:42

If the artifact was expected to capture continuously throughout that interval, the missing scheduled seconds between those timestamps must be recognized as data loss.

The exact implementation must account for:

* the 1 Hz grid boundary
* the last valid persisted frame
* the first valid frame after recovery
* configured session boundaries
* intentionally inactive phases
* any already-classified stale/write failures

Do not double-count the same missing second under multiple loss categories.

17.6 SERVER OR APPLICATION OFFLINE AT MARKET OPEN

The system must also handle cases where TickVault starts late.

Example:

scheduled capture begins:
09:15:00

server/application actually starts:
09:27:00

The period:

09:15:00 -> 09:26:59

must count as data loss.

The calculation cannot depend on having observed a frame before the outage.

The configured session itself defines when capture was expected to begin.

17.7 SERVER OR APPLICATION GOES OFFLINE UNTIL AFTER MARKET CLOSE

The system must also correctly handle an outage that never recovers during the trading session.

Example:

last valid frame:
13:40:15

server remains offline for the rest of the day

configured capture close:
15:30:00

The missing scheduled interval from the last valid frame until market close must count as data loss.

This may need to be finalized the next time TickVault starts.

The system should be able to reconstruct historical session completeness from persisted data and session configuration even when the previous process never got an opportunity to finalize its telemetry.

17.8 MANUAL SHUTDOWN DURING MARKET HOURS ALSO COUNTS AS LOSS

Do not automatically classify a graceful/manual application shutdown as harmless inactivity.

If TickVault is supposed to capture an active artifact and somebody deliberately stops:

* the application
* the container
* the capture service
* or the server

the resulting missing scheduled market seconds are still data loss.

A clean shutdown describes how the application stopped.

It does not change the fact that market observations were missed.

Only time explicitly outside the artifact's scheduled capture window should automatically be excluded from data-loss calculations.

17.9 DISTINGUISH CAPTURE DISABLED FROM SYSTEM FAILURE

There may eventually be situations where capture is intentionally disabled for an entire artifact/session through configuration.

That condition should be explicitly represented rather than inferred from the application simply not writing anything.

Conceptually:

# SCHEDULED + ENABLED + MISSING

DATA LOSS

# SCHEDULED + EXPLICITLY DISABLED BY CONFIGURATION

NOT EXPECTED DATA

# OUTSIDE SESSION

NOT EXPECTED DATA

This distinction prevents maintenance/configuration decisions from being confused with accidental outages.

The implementation should use the existing configuration architecture rather than introduce an unrelated scheduling system.

17.10 DO NOT RELY ONLY ON SEQUENCE NUMBERS FOR SERVER-DOWNTIME DETECTION

Sequence numbers remain useful for detecting holes while the process is running and generating capture grids.

However, a process cannot advance a sequence number while it is not running.

Therefore sequence discontinuity alone cannot measure full application/server downtime.

Use a combination of:

session-derived expected timestamps
persisted frame timestamps
sequence information
persisted telemetry where available

to reconstruct completeness.

Wall-clock/session time defines the expected grid.

The binary archive proves which portions of that grid actually exist.

17.11 MAINTAIN TWO LEVELS OF LOSS REPORTING

Expose at least two conceptual views:

TOTAL DATA LOSS

This answers:

"How much of the market session is missing from this dataset?"

This is the most important historical-data-quality metric.

It includes every missing expected frame regardless of cause.

LOSS BREAKDOWN

This answers:

"Why is the data missing?"

Where determinable, break the total into:

feed stale
application/server downtime
write failures
unclassified loss

The sum of classified and unclassified missing periods must reconcile with total data loss.

17.12 LOSS ACCOUNTING SHOULD BE PER ARTIFACT

Because different artifacts may eventually have different active sessions, data-loss calculations should be evaluated against each artifact's scheduled timeline.

For example, if one artifact closes while another remains active:

the closed artifact accumulates no further expected seconds

the active artifact continues accumulating expected seconds

If TickVault fails during that later period, only artifacts that were expected to be active during that interval should accumulate additional data loss.

17.13 FRONTEND/DASHBOARD REPRESENTATION

The analytics dashboard should eventually be capable of showing the difference between:

feed loss
system/application downtime
write-path loss
unknown loss

as well as the combined total.

Conceptually useful values include:

Scheduled Market Time
Captured Time
Missing Time
Feed-Stale Loss
System Downtime
Write Failure Loss
Unclassified Loss
Total Data Loss %

Do not force a particular UI design.

The existing Next.js dashboard architecture should determine the appropriate presentation.

17.14 SESSION COMPLETENESS SHOULD BE AUDITABLE AFTER THE FACT

One of TickVault's architectural goals should be:

Given only:

* the binary archive
* its timestamps/sequence metadata
* the trading date
* the relevant session configuration/calendar

TickVault should be able to determine the approximate completeness of that trading session even if runtime telemetry was partially or completely lost.

Telemetry improves diagnosis.

It must not be the sole source of truth for data completeness.

17.15 FINAL LOSS INVARIANT

The architectural invariant should be:

Every second in which TickVault was expected to persist a market frame must resolve into exactly one of two outcomes:

1. VALID FRAME EXISTS

or

2. DATA LOSS

There should be no third state where a scheduled market second silently disappears from accounting.

Outside-market and explicitly-disabled periods are not scheduled seconds and therefore do not participate in this equation.

This makes historical-data quality measurable independently of application uptime and ensures that server outages, process crashes, feed failures, and persistence failures cannot silently make a trading session appear complete.


18. FORMALIZE SNAPSHOT CONSISTENCY

Review the current relationship between:

broker callback thread
async queue
tick application
live mutable state
1 Hz snapshot task

The desired invariant is:

broker callbacks should hand data into the application's ingestion mechanism.

Only the controlled application-side update path should mutate the canonical live market state.

Snapshot creation should observe a coherent application state.

In Python/asyncio, if live-state mutation and snapshot creation are serialized through the same event loop, ensure snapshot copying itself does not yield midway through the operation unless the existing implementation explicitly guarantees consistency another way.

Do not introduce unnecessary heavy locking if the existing event-loop ownership already guarantees this property.

After inspection, document the actual snapshot consistency guarantee.


19. KEEP THE CURRENT WRITER CONCURRENCY MODEL UNLESS CODE ANALYSIS SHOWS A PROBLEM

There are currently only a small number of persistence targets.

Adding one additional index-F&O persistence target does not justify redesigning the I/O architecture.

If the existing design uses one lightweight writer thread/worker per binary target and it is performing correctly, retain it.

Do not optimize this simply for theoretical elegance.

Only modify writer concurrency if profiling or code analysis identifies an actual bottleneck or correctness issue.


20. ADD PER-ARTIFACT OBSERVABILITY

TickVault should expose health independently for each logical capture domain.

Useful conceptual telemetry includes:

current market/session phase
capture active/inactive
last relevant update age
last persisted frame
frames written
stale frames suppressed
writer failures
writer queue depth
snapshot latency
write latency
current data-loss percentage

Do not assume every metric must be added if equivalent telemetry already exists.

Analyse the current telemetry model first and extend it instead of duplicating it.


21. ADD TRANSPORT-LEVEL OBSERVABILITY

The broker/WebSocket layer should expose enough information to distinguish transport problems from market inactivity.

Relevant concepts include:

WebSocket connected state
number of subscriptions
subscription capacity
subscription utilization
packets per second
ticks per second
last packet age
ingestion queue depth
tick apply lag
unmatched-token count
reconnect/restart status

Reuse existing telemetry wherever equivalent information is already available.


22. UPDATE THE NEXT.JS DASHBOARD WITHOUT HARD-CODING THE NUMBER OF ARTIFACTS

The frontend should not assume TickVault permanently contains exactly five capture domains.

Backend status information should represent artifacts dynamically enough that the new index-F&O domain can appear without redesigning the entire dashboard.

The frontend should be able to display per-artifact:

session phase
capture state
fresh/stale state
writer status
loss statistics
recovery condition

Follow the existing TypeScript state-management and WebSocket architecture.

Do not replace the frontend architecture merely to support this additional telemetry.


23. DISTINGUISH MARKET PHASE FROM FEED HEALTH

These are independent dimensions.

Example:

Market phase:
PREOPEN

Feed health:
HEALTHY

or:

Market phase:
OPEN

Feed health:
TRANSPORT_STALE

or:

Market phase:
CLOSED

Feed health:
not applicable / inactive

Do not overload one status variable to represent both concepts.

This distinction should be reflected consistently in backend telemetry and frontend interpretation.


24. PRE-OPEN SUPPORT SHOULD BE A POLICY, NOT AN ASSUMPTION

The system should be capable of capturing an exchange pre-open phase when such data is relevant.

However, do not automatically mix every pre-open observation into every dataset.

Determine which instruments actually participate in the relevant pre-open mechanism and which TickVault datasets benefit from preserving that data.

Make pre-open persistence configurable at the appropriate session or artifact level.

This allows future research into:

- opening price discovery
- futures/spot relationships
- opening arbitrage conditions
- pre-open imbalance
- early-session lead/lag behaviour

without forcing all historical data products to adopt the same capture window.


25. MARKET CLOSING TIMES MUST BE MODELLED, NOT ASSUMED

Do not encode logic based on assumptions such as:

all artifacts close at one universal time

or:

all derivatives always have a different close from cash instruments.

The configuration and session model should be capable of representing different close times where required.

Artifacts should inherit their close behaviour from the appropriate session.

This allows exchange-session changes to be handled primarily through configuration rather than business-logic modifications.


26. PRESERVE BINARY BACKWARD COMPATIBILITY

Existing historical binary data must continue to be readable.

Do not change existing schemas solely because the scheduling/freshness architecture is being improved.

If an existing binary format genuinely needs modification:

analyse whether schema versioning is already present
determine migration impact
preserve readers for previous versions
document the compatibility implications

The preferred outcome of this work is that the current datasets remain binary-compatible while index F&O is introduced as a new data domain.


27. VALIDATE THE NEW ARCHITECTURE WITH SESSION SIMULATIONS

Before considering the change complete, test behaviour across the complete trading lifecycle.

Test process startup before market activity.

Confirm:

bootstrap succeeds
no false data loss is recorded
no recovery escalation occurs merely because trading has not started


Test pre-open.

Confirm:

only intended artifacts consider themselves active
inactive artifacts do not accumulate loss
feed-health semantics remain correct


Test normal market open.

Confirm:

required artifacts become active
1 Hz persistence begins correctly
sequence/timestamp behaviour is correct
loss accounting begins from the appropriate scheduled point


Test normal live operation.

Confirm:

all existing datasets remain unaffected
new index-F&O data is captured
writer queues remain healthy
frontend telemetry is accurate


Test complete transport failure.

Confirm:

transport stale is detected
invalid frames are suppressed
stale duration is measured
restart-first recovery activates only after the configured threshold
writers drain safely
the restarted process resumes correctly
the gap remains auditable


Test artifact-specific failure.

Simulate:

WebSocket packets continue arriving
most datasets continue updating
one logical artifact stops receiving relevant updates

Confirm:

the condition is reported as artifact-level staleness rather than immediately misclassified as complete transport failure.


Test brief recovery flicker.

Ensure a single transient update does not incorrectly erase a sustained stale spell when the existing sustained-recovery-confirmation policy says otherwise.


Test normal close.

Confirm:

artifacts transition to closed/inactive state
writer queues drain
final persistence completes
telemetry finalizes correctly
no stale alarms fire after expected close
no post-close inactivity is counted as loss
no unnecessary process restart occurs


28. PROFILE BEFORE OPTIMIZING

After adding index F&O, measure rather than guess.

Observe:

WebSocket tick rate
async ingestion queue depth
apply latency
snapshot latency
writer queue depth
write latency
CPU utilization
memory usage
binary growth rate
subscription count
frontend broadcast cost

Do not introduce additional concurrency, batching, sharding, Rust components, multiprocessing, or other optimization work unless measurements show that the Python implementation is approaching a real limit.


29. IMPLEMENTATION STRATEGY

The safest implementation order is conceptually:

First:
analyse the existing implementation and map all affected control paths.

Then:
introduce session-aware scheduling without changing binary formats.

Then:
make stale/recovery behaviour session-aware.

Then:
improve freshness semantics by separating transport, artifact, and content freshness.

Then:
correct loss accounting so only scheduled capture periods contribute to loss.

Then:
formalize/document snapshot consistency.

Then:
calculate the expanded subscription universe and validate broker-capacity headroom.

Then:
add the consolidated index-F&O capture domain using existing writer infrastructure.

Then:
extend telemetry.

Then:
extend the Next.js dashboard/status handling.

Then:
perform lifecycle, stale-feed, restart, writer, and market-session drills.

Finally:
profile the completed system and make optimizations only where measurements justify them.


30. NON-GOALS

This task is NOT authorization to:

redesign the repository
rename large parts of the codebase
rewrite functioning components
replace existing abstractions unnecessarily
rewrite the backend in another language
change the frontend framework
change the 1 Hz capture philosophy
convert storage into tick-by-tick persistence
store calculated Greeks/IV/arbitrage signals
migrate existing historical binary data without necessity
create new services merely for architectural purity
introduce multiple broker WebSockets unless subscription capacity requires them
rewrite existing writers simply because a new data domain is being added


31. FINAL ARCHITECTURAL PRINCIPLES

Preserve these principles throughout implementation:

Data domains define what is persisted.

Market sessions define when persistence is valid.

Writers persist data; writers should not own exchange-session business logic.

Transport health and market activity are not the same thing.

Artifact health and WebSocket health are not the same thing.

Scheduled inactivity is not data loss.

Stale duplicated values must not masquerade as historical observations.

Raw market state belongs in the archive.

Derived analytics belong in reconstruction and analysis.

Existing binary compatibility is valuable and should be preserved.

Configuration should describe changing operational conditions.

Business logic should not be scattered with hardcoded market times.

The existing TickVault architecture should be extended, not replaced.

Codebase analysis must determine the concrete implementation.

The goal is to make TickVault's live-capture subsystem capable of handling additional market domains and differing trading sessions cleanly while preserving the already-working pipeline and keeping future arbitrage, replay, analytics, and backtesting use cases open.

## Below can be the new env block that i need to add.
```
MARKET_TIMEZONE=Asia/Kolkata

BOOTSTRAP_TIME=08:55

EQUITY_DERIV_PREOPEN_START=09:00
EQUITY_DERIV_PREOPEN_END=09:15

EQUITY_DERIV_OPEN=09:15
EQUITY_DERIV_CLOSE=15:30

EQUITY_CASH_OPEN=09:15
EQUITY_CASH_CLOSE=15:30

CAPTURE_STALE_SECONDS=5
CAPTURE_STALE_EXIT_SECONDS=60
```
