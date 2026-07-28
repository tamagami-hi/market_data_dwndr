# TickVault Frontend Redesign PRD

## Product goal

Turn the production Next.js application into a dense, dark operator workstation that lets a market-data operator identify capture health, data loss, storage state, and stream readiness at a glance.

## Users and jobs

- Operators monitor the current or most recently retained capture session.
- Engineers diagnose stream, persistence, compression, and automation failures.
- Analysts inspect complete option-chain and stock-depth data without losing fields on small screens.

## Required outcomes

- Preserve `/`, `/monitor`, `/option-chain`, `/stocks`, and `/login`.
- Preserve every REST endpoint, WebSocket topic, envelope tag, payload meaning, environment variable, and backend behavior.
- Use a dark-only tokenized interface with cyan interaction accents. Green, amber, and red communicate operational state only.
- Present loading, empty, stale, retained, degraded, exhausted, malformed, error, and recovery states explicitly.
- Make all route workflows usable at 375px through 3200px without document overflow.
- Keep labels at least 12px, mobile targets at least 44px, keyboard focus visible, and numeric content tabular.
- Keep full option, futures, scalar, and L1-L5 depth data available on desktop and through mobile disclosures.

## Route outcomes

- Home prioritizes Capture Monitor and links to all operator destinations.
- Monitor follows the hierarchy: session context, six KPIs, alerts, live stream health and loss, session history, storage and compression, logs.
- Option Chain retains the call-strike-put desktop matrix and groups every field into mobile Price, Flow, and Greeks details.
- Stocks retains the complete matrix and live depth while providing a summary-first mobile layout.
- Downloader presents initialization as a clear automation sequence with prerequisites, current action, market phase, and actionable failures.

## Acceptance criteria

- WCAG AA contrast, skip navigation, keyboard navigation, accessible explanations, native dialog behavior, and Axe checks pass.
- INP is below 200ms, LCP below 2.5s, CLS below 0.1, and mocked 1 Hz updates produce no repeated task above 50ms.
- Changed frontend modules maintain at least 80% statements, branches, functions, and lines.
- Lint, TypeScript, unit, integration, production build, E2E, accessibility, and visual checks pass.

## Non-goals

- No API, WebSocket, schema, environment, route-slug, authentication, automation, or backend behavior changes.
- No deployment or commit.
- No speculative virtualization.
- Do not modify `backend/app/static/monitor.html`; it remains the emergency diagnostic interface.
