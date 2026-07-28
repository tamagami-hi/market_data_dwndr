---
title: Operations-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/operations]
up: "[[Home]]"
related: ["[[Decisions-MOC]]", "[[Live-Capture-MOC]]", "[[Code-Map]]"]
---

# 🗺️ Operations — MOC

> [!note] Daily lifecycle: automated login, market-hours scheduling, EOD compression,
> mid-day restart/resume, retention, and failure handling.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[operations-runbook]] | daily lifecycle: login, hours/calendar, EOD, restart | done |
| [[config-and-env]] | env vars, settings, **automated login (`md-login`)** | done |
| [[session-state]] | access_token + risk-free-rate persistence & resume | done |
| [[failure-modes]] | disconnects, auth expiry, disk full, truncated-file recovery | done |
| [[data-retention]] | raw vs compressed lifetime, integrity checks | done |
| [[vps-docker-deployment]] | private Tailscale deployment, storage preflight, systemd boot startup | done |
| [[lan-and-public-domain-routing]] | same-origin design, AdGuard DNS + nginx, LAN → public path | done |
| [[nginx-vhost-guide]] | install / update / verify the reverse-proxy vhost, troubleshooting | done |
| [[review-2026-07-28-streaming-and-dual-uplink]] | dual-ISP uplinks, netplan link-local trap, nginx default_server / Tailscale-IPv6 quirk, full L1–L5 streaming, restart-resume | done |

## Implemented in
- `backend/app/ops/{calendar,scheduler,eod,session_manager,retention}.py`
- `backend/app/kite/login.py` (`md-login`), `session.py`, `session_service.py`
- `backend/app/api/auth.py` — `/api/auth/{status,login,login-url}`
- `backend/app/main.py` — startup resume + route wiring
- `deploy/preflight.sh`, `deploy/market-data-dwndr.service.example` — mount/network checks and boot-safe Compose startup
- `deploy/nginx/tickvault.beonedge.internal.conf` — same-origin reverse-proxy vhost (annotated); see [[nginx-vhost-guide]]
- `docs/60-operations/network-topology.svg` — the four access paths in one diagram
- Tests: `test_calendar_scheduler`, `test_eod`, `test_session_manager`, `test_retention`,
  `test_login`, `test_auth_api`

Related: [[Decisions-MOC]] · [[Live-Capture-MOC]] · [[build-guide]]
