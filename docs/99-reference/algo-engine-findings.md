---
title: Algo Engine Findings
area: reference
type: reference
status: locked
tags: [area/reference, type/reference, status/locked]
up: "[[Reference-MOC]]"
related: ["[[option-chain-selection]]", "[[bin-structure-spec]]", "[[live-data-pipeline]]", "[[historical-data]]"]
source: algo_engine (Rust)
---

# Algo Engine Findings

Reference project: `algo_engine/backend_engine` (Rust) and `algo_engine/frontend_stack`
(Next.js). Concrete facts that inform the Python port. Paths are relative to
`algo_engine/backend_engine/src/` unless noted.

## Module map

| Concern | Rust location |
|---|---|
| Broker REST (quotes, LTP, instruments) | `kite_broker/rest.rs` |
| WebSocket connect + subscribe | `kite_broker/stream/reference.rs` |
| Binary tick decode | `kite_broker/stream/parser.rs` |
| Ingestion loop (WS + reconnect) | `kite_broker/stream/ingestion.rs` |
| Tick → table apply | `kite_broker/stream/processor/table_updater.rs` |
| Broadcast pipeline | `kite_broker/stream/processor.rs` |
| Option chain assembly | `oc_maker/table/assembler.rs` |
| Strike window filter | `oc_maker/table/filter.rs` |
| Option chain model | `oc_maker/types/chain.rs` |
| Binary frame writer | `backtest/writer.rs` |
| Binary frame reader/index | `backtest/reader.rs` |
| zstd compression sweep | `backtest/compressor.rs` |
| Binary frame types | `backtest/types.rs` |
| Historical → bin export | `kite_broker/historical_data/orchestrator/bin_export.rs` |
| WS protocol enum | `utils/protocol/ws_protocol.rs` |

## Broker API essentials (Kite Connect v3)

- **Auth header:** `Authorization: token {api_key}:{access_token}` + `X-Kite-Version: 3`.
- **Instruments dump:** `GET https://api.kite.trade/instruments/{exchange}` → CSV.
  Columns: `instrument_token, exchange_token, tradingsymbol, name, last_price, expiry,
  strike, tick_size, lot_size, instrument_type, segment, exchange`.
- **LTP:** `GET /quote/ltp?i=NSE:NIFTY 50`. **Full quote:** `GET /quote?i=...`.
- **Historical:** `GET /instruments/historical/{token}/{interval}?from&to&oi=1`.
- **WebSocket:** `wss://ws.kite.trade?api_key=&access_token=`; send
  `{"a":"subscribe","v":[tokens]}` then `{"a":"mode","v":["full",[tokens]]}`.

### Well-known instrument tokens
```
NIFTY 50 = 256265   NIFTY BANK = 260105   FINNIFTY = 257801
SENSEX   = 265      INDIA VIX  = 264969
```

## Binary tick packet (full mode)

Big-endian; length varies. Token (u32), last_price (i32 ÷100), then OHLC / volume /
buy-sell qty / OI / OI day hi-lo / **5 levels of depth** (each: qty i32, price i32
paise, orders i16, padding). `algo_engine` reads only **L1** (first bid at byte 64,
first ask at byte 124); we keep L1 for indices, L5 for stocks. `KiteTicker` parses all
5 levels for us.

## BIN storage facts (what we adapt)

- Frame framing `[u32 LE len][payload]`, header frame first, then data frames.
- `DailyHeader { trading_date, expiry_date, underlying }`; `CaptureFrame` carries
  `timestamp, sequence, vix, spot_price, calls, puts, strike` with a 23-column
  `ReplayBlock` (incl. Greeks).
- Historical (`bin_export.rs`) writes the same format with a `_historical` name and
  `0.0` Greeks when the source lacks them.
- Compression sweep: `compressor.rs`, **zstd level 17**.
- On read, `inflate_capture_frame` recomputes **aggregate** metrics (ATM, max-pain,
  PCR, straddles); per-strike Greeks come from disk (not recomputed on read).

## How the Python port differs

- **Own integer-native schema** (paise `i64`, counts `u64`) instead of the `f64`
  `ReplayBlock` — [[bin-structure-spec]].
- **Greeks/IV not stored** (reconstructed on read); the **risk-free rate is stored in
  the header** (algo_engine had no such header field) — [[bin-format]].
- **Depth:** indices L1 (as algo_engine), **stocks L5** (new) — [[depth-level-research]].
- **Cadence:** fixed **1 Hz** snapshots (algo_engine used its broadcast cadence).
- **Adds** the CalSpread stock board ([[stocks-capture]]); **drops** all Greeks/IV
  computation, execution, strategies, and risk.
