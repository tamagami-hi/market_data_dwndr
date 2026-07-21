---
title: Data-Storage-MOC
area: map
type: moc
status: living
tags: [area/map, type/moc, area/data-storage]
up: "[[Home]]"
related: ["[[Live-Capture-MOC]]", "[[Historical-MOC]]", "[[Code-Map]]"]
---

# 🗺️ Data & Storage — MOC

> [!note] The integer-native BIN format, on-disk layout, and read-time reconstruction.

## Notes
| Note | Purpose | Status |
|------|---------|:------:|
| [[bin-structure-spec]] | **authoritative** byte-level layout (index + stock) | done |
| [[bin-format]] | format rationale & overview | locked |
| [[storage-layout]] | `MARKET_DATA/` directory tree + instrument archive | done |
| [[lossless-and-precision]] | integer-native storage, the two lossless axes | locked |
| [[reconstruction]] | Greeks/metrics recomputed on read from raw + bond yield | done |

## Implemented in
- `backend/app/bin_codec/{layout,writer,reader,compress}.py` — the codec
- `backend/app/reconstruct/{bs,greeks,metrics,spreads}.py` — read-time derivation
- Tests: `test_layout`, `test_writer`, `test_roundtrip`, `test_compress`, `test_reconstruct_*`

Related: [[Live-Capture-MOC]] · [[Historical-MOC]] · [[depth-level-research]] · [[Quality-MOC]]
