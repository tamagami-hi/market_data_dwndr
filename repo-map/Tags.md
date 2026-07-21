---
title: Tags
area: map
type: moc
status: living
tags: [area/map, type/moc]
up: "[[Home]]"
---

# 🏷️ Tag Index

The vault uses a small, consistent tag taxonomy so Obsidian's Graph **Groups** can
color nodes by area/type/status.

## `area/*` — domain
`area/overview` · `area/architecture` · `area/data-storage` · `area/live-capture` ·
`area/historical` · `area/frontend` · `area/operations` · `area/quality` ·
`area/build` · `area/decisions` · `area/reference` · `area/logs` · `area/map`

## `type/*` — kind of note
`type/overview` · `type/plan` · `type/spec` · `type/decision` · `type/research` ·
`type/reference` · `type/moc` · `type/log` · `type/code-map` (docs↔source) ·
`type/dashboard` (status)

## `status/*` — maturity
`status/locked` (agreed, build against it) · `status/living` (updated over time) ·
`status/done` (spec implemented — see [[Code-Map]]) · `status/draft`

## `log/*` — log stream
`log/progress` · `log/change`

## Suggested Graph Groups (Obsidian → Graph view → Groups)
- color by `area/*` to see domain clusters
- filter `type/moc` to see the hub-and-spoke skeleton
- filter `status/draft` to find unfinished notes
