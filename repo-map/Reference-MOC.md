---
title: Reference-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/reference]
up: "[[Home]]"
related: ["[[Data-Storage-MOC]]", "[[Live-Capture-MOC]]", "[[Decisions-MOC]]"]
---

# 🗺️ Reference — MOC

> [!note] External source material the design was ported from and validated against.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[algo-engine-findings]] | facts extracted from the Rust `algo_engine` | reference |
| [[depth-level-research]] | L1 vs L2…L5 industry practice (with sources) | reference |

## Cross-verification
The Python ports were diffed against `algo_engine` (Rust): ATM filter, Greek
normalization, max-pain/PCR, reconnect policy, and bin export were confirmed to match;
three Black-Scholes gaps were fixed. See [[change-log]] and [[Code-Map]].

Related: [[Data-Storage-MOC]] · [[Live-Capture-MOC]] · [[Decisions-MOC]]
