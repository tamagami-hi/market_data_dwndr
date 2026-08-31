---
title: BIN Structure Spec
area: data-storage
type: spec
status: locked
tags: [area/data-storage, type/spec, status/locked, reference/authoritative]
up: "[[Data-Storage-MOC]]"
related: ["[[bin-format]]", "[[storage-layout]]", "[[lossless-and-precision]]", "[[stocks-capture]]"]
---

# BIN Structure Spec

Authoritative byte-level layout of every `.bin` file this project writes. A
**standalone format we own**, using `algo_engine`'s storage *approach* (little-endian
fixed-width encoding, `[u32 LE len][payload]` framing, one header frame then data
frames, whole-file zstd L17). Values are the API's **native integers**
([[lossless-and-precision]]); the only float is the risk-free rate.

## 1. Primitives (all little-endian)

| Notation | Bytes | Meaning |
|---|---|---|
| `u32` | 4 | unsigned int |
| `u64` | 8 | unsigned int (quantities, OI, volume, tokens, timestamps, sequence) |
| `i64` | 8 | signed int — **prices/index values in paise** (`value × 100`) |
| `f64` | 8 | IEEE-754 — used only for `risk_free_rate` |
| `String` | 8 + N | `u64` byte-length, then N UTF-8 bytes |
| `Vec<T>` | 8 + … | `u64` element-count, then the elements |
| enum tag | 4 | `u32`: `0 = Header`, `1 = Data` |

**Price convention:** every price/index/OHLC value is integer paise
(₹24567.05 → `2456705`; India VIX 12.34 → `1234`). Divide by 100 on read.

## 2. Frame framing (both file types)

```
file  := header_frame  data_frame*
frame := u32 payload_len   payload[payload_len]
```
- First frame's payload is a Header (tag `0`), written once when the file is empty.
- Every subsequent frame is a Data frame (tag `1`).
- At market close the whole file is compressed with **zstd level 17** →
  `<date>.bin.zst` (a zstd stream of the same framed bytes).

### 2.1 Axis segments (`<date>.pN.bin`)

**Index option-chain files no longer segment on an axis change.** From index schema **v2**
each frame carries its own strike vector, so `one index + one trading date = one .bin file`
regardless of how often the window changes. See §3.

Segments remain for the **stocks matrix** and the **index-F&O grid**, whose row axis is
still fixed by the header. Every data frame in those files must match the row count the
header declares. The header fixes that axis and is written only once, but the in-memory
axis is rebuilt on every bootstrap, so appending frames of a different width behind the
original header would silently mis-attribute every column. When the row axis changes,
capture **rolls to a new segment** rather than appending:

```
MARKET_DATA/STOCKS/2026-08-27.bin      # header axis 210
MARKET_DATA/STOCKS/2026-08-27.p2.bin   # header axis 209, its own header
```

- Segments are ordinary `.bin` files in the same directory, so the EOD sweep archives them
  unchanged (it globs `*.bin` recursively) and the reader opens each one normally.
- Segment selection is by axis, not by sequence: an oscillating axis reuses the segment
  that already matches, so files do not fan out (`resolve_segment_path`).
- Completeness must union the timestamps across all segments for a date — see
  `app.ops.reconcile_session.segment_paths`. This still matters for index files already on
  disk, which do have `.pN` segments from before v2.
- A generation that never receives a tick writes **no header and no file**: the header is
  what fixes the axis, so it has to be earned by data.
- Reopening a file with a mismatched axis raises `HeaderAxisMismatch` rather than appending.

For index files the only thing that still steps aside is a **schema version** difference
(`resolve_index_segment_path`): v1 and v2 frames decode differently and must never share a
file, so the first v2 run after the format change continues in a new segment and leaves the
captured v1 file byte-identical. A strike-axis change never rolls.

#### Why the index axis moved into the frame

Under v1 the header held the only strike vector. `option_chain_filter` re-centres the
ATM ± 50 window on spot and clips it against the listed strikes, so a restart could change
the window's **count** — detectable, and handled by rolling — or keep the count and change
**which strikes** it covers, which was *not* detectable at all, because the comparison was
axis length.

Both were measured in production. On 2026-08-26/27 the header came from a five-frame
generation and 99.98% of the day was mis-mapped; across the archive, 8 of 15 NIFTY sessions
were affected (~128,110 frames). On 2026-08-31 BANKNIFTY held width 101 all day while the
window slid 19 → 16 → 17 → 18 → 17 positions inside the listed universe — same count, so no
roll, and every frame silently mis-labelled. Nothing detected either case: the frames decode
fine and every counter reads healthy.

Storing the axis in the frame removes both. Equal counts no longer imply equal axes, and no
frame can be decoded against a foreign axis because it brings its own.

---

## 3. Index option-chain file  (`MARKET_DATA/INDICES/<INDEX>/<date>.bin`)

Depth = **L1**. 15 raw columns per side, plus the frame's own strike axis.

### 3.1 IndexHeader (tag 0)
```
u32       tag                = 0
u32       schema_version     = 2
String    trading_date                     // "2026-07-21"
String    underlying                       // "NIFTY"
String    expiry_date                      // "2026-07-24"
f64       risk_free_rate                   // daily rate source/fallback
```
No strike axis. A single header-level vector could only ever describe one generation.

### 3.2 IndexFrame (tag 1) — one per second
```
u32       tag                = 1
u64       timestamp_unix_ms
u64       sequence
i64       spot_price                       // paise
i64       vix                              // ×100
RawBlock  calls                            // 15 × Vec[N]
Vec<i64>  strikes                          // 1 × Vec[N], paise, strictly ascending
RawBlock  puts                             // 15 × Vec[N]
```

31 vectors, all of length `N`. `N` is the strike count **for this frame**: it may differ
from frame to frame within one file, and is at most `MAX_STRIKES_PER_FRAME` (101 = 2×50+1).

**Positional axis contract.** `strikes[i]` names the strike whose CE data occupies
`calls.<field>[i]` and whose PE data occupies `puts.<field>[i]`, for all 15 fields on each
side. Conceptually each row is one complete strike:

```
[CALL ×15]  [STRIKE ×1]  [PUT ×15]
 CALL values for strike X |  X  | PUT values for strike X
```

Data belonging to another strike must never occupy that position. `IndexFrame.validate()`
enforces it before encoding and **rejects** the frame rather than writing it: all 31 vectors
must share length `N`, and the axis must be strictly ascending (an unsorted or duplicated
axis means position `i` no longer identifies a unique strike). Upstream,
`OptionChain.validate()` checks that the `token_map` the wings are filled through was
derived from that same ordered strike list.

Legacy v1 index frames have no strike vector and take their axis from the header; readers
attach it to every frame they decode, so callers never branch on the version.

### 3.3 RawBlock (L1) — 15 columns, fixed order, each a Vec aligned to `strikes`
```
Vec<i64>  ltp            // paise
Vec<u64>  oi
Vec<u64>  volume
Vec<u64>  buy_quantity
Vec<u64>  sell_quantity
Vec<i64>  bid            // paise (L1 best bid price)
Vec<u64>  bid_qty
Vec<i64>  ask            // paise (L1 best ask price)
Vec<u64>  ask_qty
Vec<u64>  oi_day_high
Vec<u64>  oi_day_low
Vec<i64>  ohlc_open      // paise
Vec<i64>  ohlc_high
Vec<i64>  ohlc_low
Vec<i64>  ohlc_close
```
All Vecs have length = `strikes.len()`. Full table every second, both sides.

**Not stored (reconstructed on read):** `iv, delta, gamma, vega, theta, rho`, `change`,
`change_in_oi`.

---

## 4. Stocks file  (`MARKET_DATA/STOCKS/<date>.bin`)

One file/day for **all** F&O stocks, stored as a **matrix** (rows = stocks in header
order, columns = fields). Depth = **L5** for every tradeable leg (spot + each future).

### 4.1 StockHeader (tag 0)
```
u32            tag              = 0
u32            schema_version   = 1
String         trading_date
f64            risk_free_rate
Vec<StockRef>  stocks                       // matrix rows, fixed order; N = stocks.len()

StockRef {
  String        tradingsymbol               // NAME, verbatim (e.g. "M&M")
  String        name
  u64           spot_token
  u32           lot_size
  Vec<FutureRef> futures                     // 1..3, ordered [current, mid, far]
}
FutureRef { u64 token, String expiry, u32 lot_size }
```

### 4.2 StockFrame (tag 1) — one per second
```
u32           tag              = 1
u64           timestamp_unix_ms
u64           sequence
InstrColumns  spot                           // equity leg
InstrColumns  fut_current                    // futures slot 0
InstrColumns  fut_mid                        // futures slot 1
InstrColumns  fut_far                        // futures slot 2
```

### 4.3 InstrColumns — columnar across all N stocks (L5)
```
// scalar-per-stock columns, each Vec length N:
Vec<i64>  ltp            // paise
Vec<u64>  oi             // 0 for the equity leg
Vec<u64>  volume
Vec<u64>  buy_quantity
Vec<u64>  sell_quantity
Vec<u64>  oi_day_high
Vec<u64>  oi_day_low
Vec<i64>  ohlc_open      // paise
Vec<i64>  ohlc_high
Vec<i64>  ohlc_low
Vec<i64>  ohlc_close

// order-book depth, exactly 5 levels:
Vec<DepthLevel>  depth   // length 5, index 0 = best (L1) … 4 = L5

DepthLevel {             // each Vec length N
  Vec<i64>  bid_price    // paise
  Vec<u64>  bid_qty
  Vec<u32>  bid_orders
  Vec<i64>  ask_price    // paise
  Vec<u64>  ask_qty
  Vec<u32>  ask_orders
}
```

**Row alignment / missing futures:** a stock with < 3 futures has undefined values in
the unused `fut_*` slot; validity comes from `StockRef.futures.len()`. Price sentinel
for "no data" is `0`.

**Not stored (reconstructed):** live spread (`fut_mid.ltp − fut_current.ltp`), daily
spread, CalSpread summary statistics.

---

## 5. Worked sizes (raw, before zstd)

| File | Per-frame | Per day (22,500 frames) |
|---|---|---|
| Index (L1, 101 strikes) | ~24.5 KB | ~0.55 GB/index |
| Stocks (L5, ~200 stocks, spot+3 fut) | ~0.26 MB | ~5.8 GB (all stocks) |

The index frame payload is `276 + 240·N` bytes at v1 and `284 + 248·N` at v2 — the extra
`8 + 8·N` is the frame's own strike vector (its `u64` count plus `N` `i64` strikes). At
N=101 that is +816 bytes/frame and +18.8 MB/day/index raw. Measured on 2,000 frames at
zstd L17 the compressed cost is **+1.37%, about +0.09 MB/day/index**: the axis is identical
every second, so the compressor removes nearly all of it.

zstd L17 brings these down several-fold. Totals stay within budget ([[live-capture-performance]]).

## 6. Reading & reconstruction

1. If `.zst`, zstd-decode the stream.
2. Scan once: per frame read `u32 len`, record `timestamp → (offset, len)`.
3. Support nearest-timestamp binary search + random-access frame ranges.
4. Divide price columns by 100 (paise → rupees) on load.
5. Compute Greeks/IV lazily from the header `risk_free_rate` + Black-Scholes only when
   needed for display/analysis.
