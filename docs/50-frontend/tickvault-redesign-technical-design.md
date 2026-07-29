# TickVault Frontend Redesign Technical Design

## Proposed modules

```text
frontend/components/ui/        shared visual and accessibility primitives
frontend/components/monitor/   focused monitor sections, each under 400 lines
frontend/hooks/                telemetry and polling ownership
frontend/lib/monitor/          guards, severity, and view models
frontend/lib/options/          guards and immutable delta helpers
frontend/lib/stocks/           guards and row projections
frontend/test/                 Vitest setup and fixtures
```

## Tokens and shape

CSS variables define canvas; surfaces 1, 2, and 3; borders; primary, secondary, and muted text; focus; accent; success; warning; and danger. Panels use 10px radii, controls 8px, and compact status labels may use pills. The spacing unit is 4px. Numeric content uses `ui-monospace` and tabular numerals.

The shell uses a 56px opaque desktop bar. Mobile uses a brand row and four equal-width route tabs. The page container grows progressively at 1600, 1920, 2400, and 3200px without changing the root font size.

## Responsive composition

- Monitor desktop uses 8/4 live health and diagnostics, 12-column session history, then 8/4 storage and compression.
- Monitor mobile uses two-column KPIs and disclosure rows with required health fields before expansion.
- Option and stock desktop views use opaque sticky headers and sticky identity cells.
- Mobile summaries never require horizontal scrolling; their disclosures contain all secondary fields.

## Accessible interaction

The logs component uses a native `<dialog>`. Opening stores the prior focus, calls `showModal`, focuses the close control, and locks body scrolling. Closing restores focus. Native Escape and modal focus containment remain intact. `100dvh` safe insets bound the panel.

Operational explanations use a focusable/touchable disclosure primitive rather than `title`. Global `:focus-visible` supplies a cyan outline. Route metadata is supplied through route `layout.tsx` files because the route pages are client components.

## Tests and performance

Vitest uses jsdom, React Testing Library, user-event, jest-dom matchers, and V8 coverage. Puppeteer remains the E2E runner and gains Axe checks. Unit tests cover pure adapters, severity, view models, immutable changes, and row memo equality. Integration tests cover polling, visibility, stale/error/recovery, retained data, and dialog focus.

A mocked 1 Hz render benchmark records update duration and long tasks. `@tanstack/react-virtual` is added only if p95 exceeds 50ms or repeated long tasks occur.

## Live-workstation refinement: daily events and option density

Operational event ownership moves to a client provider above both navigation and
routed content. The provider reuses the existing ref-counted `capture-status`
and `session` connections; it does not subscribe globally to the large option or
stock payloads. It stores normalized, plain-text event metadata under a
versioned Asia/Kolkata day key in `localStorage`, validates data while hydrating,
and resets atomically at the next market-local midnight. Web Locks select one
collector tab, with an expiring browser-storage lease as the compatibility
fallback. Event IDs are unique per observed episode; initial state snapshots are
compared with the latest persisted transition so handoffs do not duplicate an
incident. Read receipts use a separate monotonic ID set, while storage-event
merges repair the event union and deliver the same seven-second alert in follower
tabs. The Monitor consumes
the provider's complete current-day observed log list, while the navigation
consumes curated notifications. A new notification is announced for seven
seconds and remains in timestamped, newest-first history until the day rolls
over. Monitor REST polling failures and recovery transitions use this channel
instead of inserting transient error cards between workstation sections.

This frontend-only design retains every message observed while at least one
workstation tab is connected, including across route changes and reloads. It
cannot reconstruct messages emitted while all tabs were closed because the
unchanged backend protocol has no historical log replay.

Frame-integrity progress uses a clamped continuous hue interpolation from red at
0%, through yellow at 50%, to green at 100%. Option rows retain every supplied
strike and remain vertically virtualized. Desktop option cells use a larger
minimum numeric size inside a centered, bounded-width table so increased
readability does not recreate ultrawide column gaps. Spot, ATM, max-pain, all
three pairings, and the three-way overlap receive seven deterministic visual
variants while preserving their compact text codes and full accessible labels.

## Backend strike-range diagnosis

The backend deliberately fixes each index chain at capture bootstrap to the
nearest-expiry instrument-master strikes within 50 listed strikes on either side
of the bootstrap ATM (101 maximum). Boundary handling clamps the slice without
shifting it to refill a short side, and the fixed strike vector does not recenter
when live ATM moves. The WebSocket broadcaster serializes the complete selected
vector without a second range or count filter. Therefore the frontend must show
every supplied strike and must not invent missing rows. Changing to a refilled
101-strike window would be a separate backend/session-format decision and may
only be applied before a session starts because the binary header and token
indices are fixed for that session.
