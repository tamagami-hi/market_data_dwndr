---
title: Home
area: map
type: moc
status: living
tags: [area/map, type/moc]
---

# 🏠 market_data_dwndr — Home

Root **Map of Content** for the knowledge base. Open the **repo root**
(`market_data_dwndr/`) as an Obsidian vault so links across `docs/`, `logs/`, and
`repo-map/` resolve. See [[vault-guide]].

## What this project is
A Zerodha Kite **market-data downloader** (no trading): live option chains (indices,
L1) + F&O stock calendar-spread boards (stocks, L5), captured at **1 Hz** into an
integer-native binary format, plus historical candles. Start at [[overview-and-scope]].

## Maps of Content
- [[Overview-MOC]] — scope, plan, build guide
- [[Architecture-MOC]] — stack, efficiency
- [[Data-Storage-MOC]] — BIN format, layout, precision, reconstruction
- [[Live-Capture-MOC]] — option chain, pipeline, stocks, performance
- [[Historical-MOC]] — historical downloader
- [[Frontend-MOC]] — UI + capture monitor + WS protocol
- [[Operations-MOC]] — runbook, config/.env, session-state, failure modes, retention
- [[Quality-MOC]] — testing strategy
- [[Decisions-MOC]] — locked decisions & open items
- [[Reference-MOC]] — algo_engine & depth research
- [[Logs-MOC]] — progress & change logs

## Fast path for a new reader
[[overview-and-scope]] → [[bin-structure-spec]] → [[live-data-pipeline]] →
[[stocks-capture]] → [[build-guide]] → [[decisions-and-open-questions]].

## Build
The phase/batch execution checklist is [[build-guide]]; operations are in
[[operations-runbook]].

## Status
Planning complete; build starts at Phase 1 (BIN codec) per [[build-guide]]. See
[[progress-log]].
