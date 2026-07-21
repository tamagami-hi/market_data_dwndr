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
| [[config-and-env]] | env vars, settings, **automated login (`md-login`)**, static IP | done |
| [[session-state]] | access_token + bond-yield persistence & resume | done |
| [[failure-modes]] | disconnects, auth expiry, disk full, truncated-file recovery | done |
| [[data-retention]] | raw vs compressed lifetime, integrity checks | done |

## Implemented in
- `backend/app/ops/{calendar,scheduler,eod,session_manager,retention}.py`
- `backend/app/kite/login.py` (`md-login`), `session.py`, `session_service.py`
- `backend/app/api/auth.py` — `/api/auth/{status,login,login-url}`
- `backend/app/main.py` — startup resume + route wiring
- Tests: `test_calendar_scheduler`, `test_eod`, `test_session_manager`, `test_retention`,
  `test_login`, `test_auth_api`

Related: [[Decisions-MOC]] · [[Live-Capture-MOC]] · [[build-guide]]
