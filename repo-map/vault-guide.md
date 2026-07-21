---
title: Vault Guide
area: map
type: reference
status: living
tags: [area/map, type/reference]
up: "[[Home]]"
---

# 📖 Vault Guide — how to open & read this

## Open it
Open the **repo root** `market_data_dwndr/` as an Obsidian vault (Open folder as
vault). Wikilinks resolve by note name across `docs/`, `logs/`, and `repo-map/`, so the
whole thing is one graph.

## Structure
```
market_data_dwndr/
├── docs/         # knowledge & plan (domain folders 00–99)
├── logs/         # progress-log, change-log, handoff
├── repo-map/     # this folder: Home + area MOCs + Code-Map + Build-Status + Tags
├── backend/      # Python/FastAPI service (the implementation)
└── frontend/     # Next.js dashboard
```

Domain folders in `docs/`: `00-overview`, `10-architecture`, `20-data-and-storage`,
`30-live-capture`, `40-historical`, `50-frontend`, `60-operations`, `70-quality`,
`90-decisions`, `99-reference`.

## Navigate
- Start at [[Home]] → jump to an area MOC → open notes.
- [[Code-Map]] links every spec note to the module(s) that implement it and the tests
  that cover it. [[Build-Status]] is the phase dashboard.
- Every note has frontmatter: `up:` (its MOC), `related:` (siblings), `tags:`.
- Use [[Tags]] to drive Graph **Groups** (color by `area/*`, filter `type/moc`).

## Conventions
- **Wikilinks** `[[note-name]]` for all cross-references (filenames are unique, no
  numeric prefixes on the note files themselves).
- **Callouts** (`> [!note]`, `> [!success]`, `> [!warning]`) flag status and gotchas.
- **`status/locked`** = an agreed decision to build against; **`status/living`** =
  evolves (MOCs, logs); **`status/done`** = spec implemented (see [[Code-Map]]).
- Progress/decisions are logged in [[progress-log]] and [[change-log]].

## Optional: Dataview
If the **Dataview** community plugin is installed, MOCs render live indexes. Without it
the same information is present as static tables/links, so the vault reads fine either
way. Example query (list every note in an area):

```dataview
TABLE status, type FROM #area/data-storage SORT file.name
```

## Graph tip
In Graph view, enable Groups: one group query `tag:#type/moc` (hubs) and one per
`tag:#area/...` (domain colors). This yields a clean hub-and-spoke map with domain
clusters.
