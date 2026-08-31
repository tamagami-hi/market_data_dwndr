# MARKET_DATA_BUS

In-house realtime market-data fan-out. Deploys to `/srv/dev_stack/MARKET_DATA_BUS`.

```
Kite → TickVault TickerBridge → tables + BIN writer (authoritative archive)
                                     │
                                     └── non-blocking PUSH ──► MARKET_DATA_BUS
                                                                 PULL ingest
                                                                     │
                                                                 PUB fan-out
                                                        ┌────────────┼────────────┐
                                                       SUB          SUB          SUB
                                                     BOE app     algo app     future svc
```

**The bus is an additional distribution output, never a dependency of TickVault capture or
persistence.** If it dies, restarts, fills its HWM, or has no subscribers: capture
continues, BIN writing continues, feed-health logic is untouched. A bus outage shows up as
degraded bus telemetry, never as capture loss.

## Scope

Does: receive normalized tick batches, stamp bus ordering metadata, republish to
subscribers, answer a health probe.

Does **not**: maintain option-chain tables, build BIN frames, write market data to disk,
compute Greeks, own Kite authentication, hold any credential, touch TickVault session
state, trade, or contain strategy logic.

## Why TCP on an internal Docker network, not `ipc://`

The old `distributed-capture-architecture-plan.md` assumed a shared `ipc://` socket
directory. Measured against the actual deployment, that is the fragile option:

| Evidence | Consequence for `ipc://` |
|---|---|
| Participating containers run as **three different uids**: `market-data-dwndr-backend` = `10001:10001`, `boe-dev-backend` = `node`, `boe-dev-app` = `101:101` | A shared socket dir would need world-writable (0777) permissions, or a shared supplementary group baked into three independently built images |
| **No shared network today**: `data-downloader_default` vs `boe_dev_frontend` / `boe_dev_internal` — separate Compose projects, separately deployed and rolled back | Coupling them through a host filesystem path adds a cross-stack dependency that neither release manager owns |
| TickVault's container is `read_only: true` | The socket directory needs a dedicated writable bind mount purely for IPC |
| ZMQ `ipc://` leaves the socket file behind on a hard kill | A stale file blocks rebind, so bus restart becomes order-dependent |

TCP on a dedicated `mdbus` bridge network avoids all four: no uid alignment, no shared
filesystem, no stale files, and ZMQ reconnects automatically so either side can restart
independently.

**No public exposure.** `compose.yaml` publishes **no ports to the host** — the sockets are
reachable only by containers attached to the external `mdbus` network. The existing
Tailscale/internal-host boundary remains the security perimeter, so there is no TLS layer.

## Wire protocol v1

3-part ZMQ multipart: `[topic] [BusHeader] [payload]`. Little-endian, fixed-width integers,
no floats anywhere in a tick. Prices are **integer paise** exactly as TickVault stores them.

Topics: `market.tick`, `market.session`.

**BusHeader — 32 bytes**

| off | type | field |
|---:|---|---|
| 0 | u16 | `protocol_version` (1) |
| 2 | u16 | `message_type` (1 = tick batch, 2 = session) |
| 4 | u16 | `count` (ticks in batch; 0 for session) |
| 6 | u16 | `reserved` (0) |
| 8 | u64 | `epoch` — producer/bus run id |
| 16 | u64 | `sequence` — 1-based, monotonic within the epoch |
| 24 | u64 | `publish_unix_ns` |

**NormTick — 360 bytes** (120-byte fixed part, then 2 × 5 depth levels × 24 bytes)

| off | type | field |
|---:|---|---|
| 0 | u64 | `instrument_token` |
| 8 | i64 | `last_price` (paise) |
| 16 | i64 | `average_traded_price` (paise) |
| 24 | u64 | `volume_traded` |
| 32 | u64 | `total_buy_quantity` |
| 40 | u64 | `total_sell_quantity` |
| 48 | u64 | `oi` |
| 56 | u64 | `oi_day_high` |
| 64 | u64 | `oi_day_low` |
| 72 | i64 | `ohlc_open` (paise) |
| 80 | i64 | `ohlc_high` (paise) |
| 88 | i64 | `ohlc_low` (paise) |
| 96 | i64 | `ohlc_close` (paise) |
| 104 | u64 | `exchange_timestamp` (epoch seconds) |
| 112 | u64 | `last_trade_time` (epoch seconds) |
| 120 | `DepthLevel[5]` | bid side |
| 240 | `DepthLevel[5]` | ask side |

`DepthLevel` = `i64 price` (paise), `u64 quantity`, `u32 orders`, `u32 pad`.

The field set was re-derived from what `app/kite/ticks.py` actually normalizes today. The
old plan's proposed 336-byte layout was **not** adopted; this is 360 bytes and carries the
two exchange timestamps a consumer cannot reconstruct.

`market.session` payload is UTF-8 JSON: `protocol_version`, `trading_date`, `producer`,
`epoch`, `instrument_count`, and `instruments[]` with `instrument_token`, `exchange`,
`tradingsymbol`, `instrument_type`, `domain`, `underlying`, `expiry`, `strike_paise`,
`option_side`. JSON because it is low-rate and must stay readable and extensible; strike
stays integer paise so the no-floats rule holds. **No credentials ever appear here.**

## Ordering and loss

Two independent domains, neither related to BIN frame `sequence`:

* **producer** epoch/sequence — set by TickVault; the bus reports upstream restarts and
  gaps on the PUSH hop;
* **bus** epoch/sequence — stamped on every outbound message; this is what subscribers
  track. The bus epoch changes when the bus restarts.

A consumer classifies each message with `(epoch, sequence)`:

```
same epoch, sequence == last + 1   → in order
same epoch, sequence  > last + 1   → gap (PUB/SUB dropped it)
different epoch                    → producer or bus restarted; reset expectations
unknown protocol_version           → refuse, do not guess
```

`SequenceTracker` in `mdbus/protocol.py` implements exactly this and is the reference for
other languages.

**PUB/SUB is intentionally lossy.** A slow subscriber loses messages and cannot apply
backpressure. No metric here claims a subscriber received anything, because ZMQ PUB cannot
prove it. Anything needing guaranteed history reads **TickVault's BIN archive**, which
remains the authoritative source.

## Configuration

See `.env.example`. `MDBUS_INGEST_ENDPOINT`, `MDBUS_PUBLISH_ENDPOINT`,
`MDBUS_HEALTH_ENDPOINT`, `MDBUS_RCVHWM`, `MDBUS_SNDHWM`, `MDBUS_LOG_LEVEL`.

TickVault side (`backend/.env`): `MARKET_DATA_BUS_ENABLED` (default `false`),
`MARKET_DATA_BUS_INGEST_ENDPOINT`, `MARKET_DATA_BUS_SNDHWM`,
`MARKET_DATA_BUS_SESSION_INTERVAL_SECONDS`.

## Health and metrics

`REP` socket on `MDBUS_HEALTH_ENDPOINT` returns a JSON snapshot; `mdbus/healthcheck.py` is
the container healthcheck. It asserts the bus is up and serving, deliberately **not** a
tick rate — outside market hours zero is correct, and failing on quiet would restart a
working service every evening.

Reported: `bus_up`, `current_bus_epoch`, `messages_in_total`, `messages_out_total`,
`ticks_in_total`, `ticks_out_total`, `session_messages_total`, `publish_dropped_total`,
`protocol_errors_total`, `producer_epochs_seen`, `producer_gaps_total`,
`producer_missed_total`, `last_message_age_ms`, `last_session_age_ms`,
`messages_per_second`, `ticks_per_second`.

TickVault reports its own side under `capture.global.transport.market_data_bus`:
`bus_publish_total`, `bus_publish_dropped`, `bus_publish_errors`, `bus_ticks_published`,
`bus_ticks_skipped`, `bus_queue_dropped`, `bus_queue_depth`, `bus_last_success_age_ms`,
`epoch`, `sequence`, `connected`.

## Deployment

```bash
# once, on the VPS: the shared network the bus and its consumers attach to
docker network create mdbus

# ship the bundle to /srv/dev_stack/MARKET_DATA_BUS, then
cd /srv/dev_stack/MARKET_DATA_BUS
cp .env.example .env          # adjust if the default ports clash
docker compose up -d --build
docker compose ps             # expect market-data-bus healthy
```

Attach TickVault to the same network (its Compose project keeps its own network too), then
set `MARKET_DATA_BUS_ENABLED=true` and `MARKET_DATA_BUS_INGEST_ENDPOINT=tcp://market-data-bus:7301`
in `backend/.env` and restart the backend.

The bus lifecycle is independent: TickVault may start first, and the bus may restart while
capture continues.

## Shadow rollout

Do **not** let another application depend on the bus until it has been validated against a
live session.

```bash
# 1. bus up, TickVault publishing, nothing consuming yet
# 2. run the diagnostic consumer for a full session
docker compose exec market-data-bus python -m mdbus.shadow_consumer tcp://127.0.0.1:7302
```

Judge the session on: tick counts vs TickVault's own `ticks_received`; `sequence gaps`;
`publish_dropped_total` and TickVault's `bus_queue_dropped`; publish→receive latency;
`tokens without a map` (must be 0); TickVault capture completeness unchanged versus a
no-bus session; and CPU/RAM delta.

The BIN archive stays authoritative throughout. Only once the stream is proven stable
should BOE applications treat it as their realtime source.

## Measured behaviour

Live feed, 2026-08-31 (from TickVault telemetry): 11,826,853 ticks over 22,608 s =
**~523 ticks/s average**, 52,585 batches (~225 ticks/batch), 2,018 tokens.

Load test, full PUSH → PULL → PUB → SUB hop:

| target | delivered | drops | proto errors | hop latency | payload |
|---|---|---|---|---|---|
| 10,000 t/s | 99,000 ticks @ 8,244/s | 0 | 0 | p50 0.171 ms, p99 0.410 ms | 2.97 MB/s |
| 50,000 t/s | 399,600 ticks @ 39,928/s | 0 | 0 | p50 0.065 ms, p99 3.067 ms | 14.37 MB/s |

≈**76× the measured average with zero loss.**

Capture impact, paced at 2,000 ticks/s (3.8× the live average), `apply_ticks` over 101
strikes, batch 225:

| configuration | p50 | p95 | p99 | applied |
|---|---|---|---|---|
| bus disabled | 0.778 ms | 1.340 ms | 1.809 ms | 12,150 |
| bus enabled, live subscriber | 0.783 ms (**+0.6%**) | 1.259 ms | 1.319 ms | 12,150 |
| bus enabled but **down** | 0.847 ms (**+8.8%**) | 1.568 ms | 1.884 ms | 12,150 |

Applied tick counts are identical in all three. Encoding runs on a dedicated publisher
thread behind a bounded queue, so the tick-consumer thread pays only an `O(1)` enqueue —
an earlier inline-encoding version cost **+382%** per batch and was rejected.
