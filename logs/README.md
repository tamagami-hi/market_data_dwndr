---
title: Logs — Convention
area: logs
type: reference
status: living
tags: [area/logs, type/reference, status/living]
up: "[[Logs-MOC]]"
related: ["[[progress-log]]", "[[change-log]]"]
---

# Logs

This folder tracks **progress** and **changes** as the project is built. It is part of
the Obsidian vault (see [[Logs-MOC]]) so the graph shows work over time.

## Files

- **[[progress-log]]** — chronological, append-only. One entry per working session:
  what was done, what's next, any blockers. Newest entry at the top.
- **[[change-log]]** — decisions and notable changes (design, schema, scope). Each
  entry: date, area, what changed, why, and links to the affected notes.

## Entry conventions

- **Dates** in `YYYY-MM-DD`.
- **Link** to the docs/code a change touches using wikilinks (e.g. `[[bin-structure-spec]]`).
- **Tags:** `#log/progress` or `#log/change`; add `#area/*` to place it on the graph.
- Keep entries short and factual; the docs hold the full detail.
