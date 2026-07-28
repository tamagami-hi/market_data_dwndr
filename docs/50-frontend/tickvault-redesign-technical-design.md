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
