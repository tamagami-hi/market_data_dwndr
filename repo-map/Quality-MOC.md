---
title: Quality-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/quality]
up: "[[Home]]"
related: ["[[build-guide]]", "[[Build-Status]]", "[[Code-Map]]"]
---

# 🗺️ Quality — MOC

> [!note] Test strategy mapped to build DoD gates. **28 pytest modules, 159 tests.**

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[testing-strategy]] | unit / integration / acceptance tests vs DoD gates | done |

## Implemented in
- `backend/tests/` — 28 modules (see [[Code-Map]] for the spec → test mapping)
- `backend/tests/conftest.py` — deterministic frame/tick builders
- Lint/format: `ruff` (backend), `eslint` + `next build` (frontend)

## How to run
```bash
cd backend && pytest -q          # 159 tests
cd backend && ruff check app tests
cd frontend && npm run build && npm run lint
```

Related: [[build-guide]] · [[Build-Status]] · [[Data-Storage-MOC]]
