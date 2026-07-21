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
├── logs/         # progress-log, change-log
└── repo-map/     # this folder: Home + area MOCs + Tags (the graph hub)
```

Domain folders in `docs/`: `00-overview`, `10-architecture`, `20-data-and-storage`,
`30-live-capture`, `40-historical`, `50-frontend`, `90-decisions`, `99-reference`.

## Navigate
- Start at [[Home]] → jump to an area MOC → open notes.
- Every note has frontmatter: `up:` (its MOC), `related:` (siblings), `tags:`.
- Use [[Tags]] to drive Graph **Groups** (color by `area/*`, filter `type/moc`).

## Conventions
- **Wikilinks** `[[note-name]]` for all cross-references (filenames are unique, no
  numeric prefixes on the note files themselves).
- **`status/locked`** = an agreed decision to build against; **`status/living`** =
  evolves (MOCs, logs).
- Progress/decisions are logged in [[progress-log]] and [[change-log]].

## Graph tip
In Graph view, enable Groups: one group query `tag:#type/moc` (hubs) and one per
`tag:#area/...` (domain colors). This yields a clean hub-and-spoke map with domain
clusters.
