---
title: Decisions-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/decisions]
up: "[[Home]]"
related: ["[[change-log]]", "[[overview-and-scope]]", "[[Reference-MOC]]"]
---

# 🗺️ Decisions — MOC

> [!note] The locked decision table plus decisions taken during the build.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[decisions-and-open-questions]] | locked decisions + minor open items | locked |

## Decisions taken during the build (see [[change-log]])
- **Raw-only `.bin`** — no stored Greeks/IV; recompute on read (vs algo_engine storing them)
- **Custom struct packing** — not bincode (own reader/toolchain)
- **Per-index ATM step** (50/100) — generalized beyond algo_engine's fixed 50
- **BS parity fixes** — 365.25-day year, intrinsic-value tolerance, VIX fallback IV
- **Env-seeded creds + automated login** — not a database (vs algo_engine's Postgres)

Related: [[change-log]] · [[overview-and-scope]] · [[Reference-MOC]]
