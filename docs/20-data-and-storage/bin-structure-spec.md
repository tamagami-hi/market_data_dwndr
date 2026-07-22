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

---

## 3. Index option-chain file  (`MARKET_DATA/INDICES/<INDEX>/<date>.bin`)

Depth = **L1**. 15 raw columns per side.

### 3.1 IndexHeader (tag 0)
```
u32       tag                = 0
u32       schema_version     = 1
String    trading_date                     // "2026-07-21"
String    underlying                       // "NIFTY"
String    expiry_date                      // "2026-07-24"
f64       risk_free_rate                   // risk-free rate (login entry)
Vec<i64>  strikes                          // ~101 strikes in paise, ascending, fixed for the day
```

### 3.2 IndexFrame (tag 1) — one per second
```
u32       tag                = 1
u64       timestamp_unix_ms
u64       sequence
i64       spot_price                       // paise
i64       vix                              // ×100
RawBlock  calls
RawBlock  puts
```

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

zstd L17 brings these down several-fold. Totals stay within budget ([[live-capture-performance]]).

## 6. Reading & reconstruction

1. If `.zst`, zstd-decode the stream.
2. Scan once: per frame read `u32 len`, record `timestamp → (offset, len)`.
3. Support nearest-timestamp binary search + random-access frame ranges.
4. Divide price columns by 100 (paise → rupees) on load.
5. Compute Greeks/IV lazily from the header `risk_free_rate` + Black-Scholes only when
   needed for display/analysis.
