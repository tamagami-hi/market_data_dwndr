# Daily automation task list

- [ ] Add RED boundary, retry, rate-age, lifecycle, depth, and release-bundle tests.
- [ ] Extend env validation and backward-compatible session state.
- [ ] Add broker-only session acquisition and risk-free-rate update API.
- [ ] Wire DailyAutomationService into FastAPI lifespan.
- [ ] Guard scheduled/manual capture readiness and serialize stop-before-EOD.
- [ ] Expose automation and rate freshness messaging in the frontend.
- [ ] Fetch and render stock L1–L5 depth on demand without slowing capture.
- [ ] Make frontend WS publication best-effort so ingestion and BIN writes have priority.
- [ ] Add `release_manager/DATA_DOWNLOADER` artifact and release workflow.
- [ ] Update env examples, operations docs, and security guidance.
- [ ] Run ≥80% coverage, unit/integration/E2E, builds, image smoke, and reviews.
- [ ] Commit and push only after secrets and ignored env files are verified absent.
