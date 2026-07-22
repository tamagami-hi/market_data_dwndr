---
title: Next-Session Handoff
area: logs
type: reference
status: living
tags: [area/logs, type/reference, status/living]
up: "[[Logs-MOC]]"
related: ["[[daily-automation-architecture]]", "[[failure-modes]]", "[[vps-docker-deployment]]", "[[Home]]"]
---

# Next-Session Handoff

## Current state

The private home-VPS workflow is implemented on `ai-dev/made`:

- The separate browser operator-auth layer and manual browser capture controls are gone.
- `DailyAutomationService` owns broker polling, market-hours capture, stop/flush, and EOD.
- Kite REST or ticker authentication failures are typed separately from writer/runtime
  failures. Capture flushes, invalidates only the exact rejected session token, and
  automatically returns to the existing HTTPS token-broker path. A validated replacement
  restarts capture on the same daily files without restarting the backend.
- `MARKET_HOLIDAYS` supplies exchange closure dates to all production calendars, in
  addition to weekend and open/close checks.
- `deploy/market-data-dwndr.service.example` starts existing Compose release images only
  after Tailscale, Docker, network readiness, and required storage mounts pass preflight.
- Release-maintenance authentication and WebSocket `FRONTEND_URL` Origin checks remain.
- Dependency and runtime version files were not changed.

## Remaining external validation

Live Kite REST/WebSocket validation still requires real credentials and a whitelisted
static-egress path. The implementation is covered with injected clients/tickers and
focused tests; do not place credentials or broker passcodes in tracked files.

## VPS activation

Follow [[vps-docker-deployment]] to populate `MARKET_HOLIDAYS`, deploy immutable images,
and install/enable the systemd unit. Continue using the release manager for updates and
rollback; the boot unit deliberately uses `docker compose up -d --no-build`.

## Branch workflow

- `main` is the stable/default branch.
- `ai-dev/made` is the working branch; review it through a pull request.
- Never commit `.env`, daily session files, captured data, or broker secrets.
