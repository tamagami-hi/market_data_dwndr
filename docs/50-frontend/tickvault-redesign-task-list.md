# TickVault Frontend Redesign Task List

## Research and baseline

- [x] Audit routes, backend contracts, WebSocket ownership, and current responsive tests.
- [x] Search GitHub, package registries, and primary library documentation for reusable setup patterns.
- [x] Record the diagnostic-only fallback monitor as out of scope.

## RED, GREEN, refactor

- [x] Add Vitest, RTL, user-event, jsdom, jest-dom, V8 coverage, and Axe dependencies.
- [x] Migrate existing unit tests and make the baseline green.
- [x] RED: adapters, severity, view models, immutable option deltas, polling lifecycle, visibility, recovery, and row memo tests.
- [x] GREEN: semantic shell and shared primitives.
- [x] GREEN: `useMonitorTelemetry` and focused Monitor sections.
- [x] GREEN: responsive Option Chain and stable memoized rows.
- [x] GREEN: responsive Stocks and complete live depth disclosures.
- [x] GREEN: Home launchpad and Downloader automation stepper.
- [x] Refactor until files are focused, functions are small, and repeated patterns use shared primitives.

## Verification

- [x] `npm run lint`
- [x] `npm run typecheck`
- [x] `npm test`
- [x] `npm run test:coverage` with all four thresholds at or above 80%
- [x] `npm run build`
- [x] E2E at 375, 393, 412, 768, 1024, 1440, 1600, 1920, 2400, and 3200px
- [x] No document overflow, complete disclosures, sticky identities, and 44px mobile targets
- [x] Keyboard navigation, native dialog focus, Axe scans, and screenshot comparisons
- [x] Mocked 1 Hz benchmark, no repeated task above 50ms, and historical sections remain stable
- [x] Code-reviewer and security-reviewer audits with all critical and high findings resolved
- [ ] Measure INP, LCP, and CLS on the private-network deployment (deferred because this change is not being deployed).

## Rollout constraints

- [x] Do not add virtualization unless the measured gate fails.
- [x] Do not modify REST, WebSocket, payload, environment, route, or backend behavior.
- [x] Do not modify `backend/app/static/monitor.html`.
- [x] Do not deploy or commit.
