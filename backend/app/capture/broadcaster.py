"""Broadcast live capture state to the frontend WS topics.

Capture stores *raw* integers; the frontend wants a display-ready option chain with
Greeks. This broadcaster reconstructs IV/Greeks on the fly (see ``reconstruct``) and
pushes tagged envelopes each capture tick:

    market-data   -> MarketHeader + OptionGrid (per index)
    stocks        -> StockBoard (spot + futures + calendar spread)
    capture-status-> CaptureStatus (from the monitor)
    session       -> Heartbeat

Prices are converted paise -> rupees for display. ``change_in_oi`` is the intraday OI
delta since the previous broadcast (we don't store a prior-day baseline).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass

from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    INSTR_SCALAR_COLUMNS,
    IndexFrame,
    IndexHeader,
    RawBlock,
    StockFrame,
)
from app.capture.snapshot import CaptureSnapshot
from app.chain.table import IndexTable
from app.reconstruct.greeks import reconstruct_greeks
from app.reconstruct.metrics import reconstruct_chain_metrics
from app.reconstruct.spreads import daily_spread, live_spread
from app.session import now_ms
from app.stocks.matrix import StockMatrix
from app.ws import protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StockDisplayRef:
    tradingsymbol: str
    name: str
    future_expiries: tuple[str, ...]


def _copy_header(header: IndexHeader) -> IndexHeader:
    """Return a header owned exclusively by the display worker."""
    return IndexHeader(
        trading_date=header.trading_date,
        underlying=header.underlying,
        expiry_date=header.expiry_date,
        risk_free_rate=header.risk_free_rate,
        strikes=header.strikes.copy(),
        schema_version=header.schema_version,
    )


def _rupees(paise: int) -> float:
    return int(paise) / 100.0


def _finite(x: float, decimals: int = 6) -> float:
    """Coerce a float to a JSON-safe finite number.

    ``json.dumps`` emits bare ``Infinity``/``NaN`` for non-finite floats, which
    ``JSON.parse`` rejects — one bad value would drop the *entire* frame in the
    browser. Greeks can legitimately go non-finite (e.g. ``gamma = pdf / (spot *
    sigma * sqrt_t)`` overflows for a tiny sigma), so guard both cases, not just NaN.
    """
    if x is None:
        return 0.0
    value = float(x)
    if not math.isfinite(value):
        return 0.0
    return round(value, decimals)


def _build_grid_block(raw: RawBlock, greeks_side: dict, prev_oi: list[int] | None) -> dict:
    n = raw.length()
    oi = [int(v) for v in raw.columns["oi"]]
    change_in_oi = [oi[i] - (prev_oi[i] if prev_oi else oi[i]) for i in range(n)]
    return {
        "oi": oi,
        "change_in_oi": change_in_oi,
        "volume": [int(v) for v in raw.columns["volume"]],
        "iv": [_finite(v * 100, 4) for v in greeks_side["iv"]],
        "delta": [_finite(v, 4) for v in greeks_side["delta"]],
        "gamma": [_finite(v, 6) for v in greeks_side["gamma"]],
        "theta": [_finite(v, 4) for v in greeks_side["theta"]],
        "vega": [_finite(v, 4) for v in greeks_side["vega"]],
        "rho": [_finite(v, 4) for v in greeks_side["rho"]],
        "bid": [_rupees(v) for v in raw.columns["bid"]],
        "ask": [_rupees(v) for v in raw.columns["ask"]],
        "ltp": [_rupees(v) for v in raw.columns["ltp"]],
        "change": [_finite(v, 2) for v in greeks_side["change"]],
    }


class Broadcaster:
    """Builds and pushes frontend messages from live capture state."""

    def __init__(
        self,
        index_tables: dict[str, IndexTable],
        stock_matrix: StockMatrix | None,
        hub,
        monitor=None,
        *,
        clock=now_ms,
        stats_state_dir=None,
        trading_date: str | None = None,
        snapshot_interval_ms: int = 60_000,
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.hub = hub
        self.monitor = monitor
        self._clock = clock
        self._stats_state_dir = stats_state_dir
        self._trading_date = trading_date
        self._snapshot_interval_ms = snapshot_interval_ms
        self._last_snapshot_write_ms: int | None = None
        self._prev_oi: dict[str, tuple[list[int], list[int]]] = {}
        self._index_headers = {
            name: _copy_header(table.header()) for name, table in index_tables.items()
        }
        self._stock_refs = tuple(
            _StockDisplayRef(
                tradingsymbol=ref.tradingsymbol,
                name=ref.name,
                future_expiries=tuple(future.expiry for future in ref.futures),
            )
            for ref in (stock_matrix.stock_refs if stock_matrix is not None else ())
        )
        self._latest_snapshot: CaptureSnapshot | None = None
        self._publish_task: asyncio.Task[None] | None = None
        # Server-side build timings for the most recent publish, also surfaced on every
        # message's `meta` (see _build_snapshot_messages for the measurement window).
        self.last_build_ms = 0.0
        self.last_greeks_ms = 0.0
        self.last_stocks_ms = 0.0
        self.last_queue_ms = 0
        # Throttled visibility for recurring best-effort broadcast failures (the
        # publish fires at the capture cadence, so we must not log a traceback every
        # tick — but we must not swallow them silently either).
        self._broadcast_fail_count = 0
        self._last_broadcast_fail_log_ms: int | None = None

    # -- message builders (pure) ------------------------------------------- #

    def index_messages(self, name: str, table: IndexTable, ts: int) -> list[dict]:
        header = table.header()
        frame = IndexFrame(ts, table.sequence, table.spot_price, table.vix, table.calls, table.puts)
        return self._index_frame_messages(name, header, frame)

    def _index_frame_messages(
        self,
        name: str,
        header: IndexHeader,
        frame: IndexFrame,
    ) -> list[dict]:
        greeks = reconstruct_greeks(frame, header)
        metrics = reconstruct_chain_metrics(frame, header)

        prev = self._prev_oi.get(name)
        calls_block = _build_grid_block(frame.calls, greeks["calls"], prev[0] if prev else None)
        puts_block = _build_grid_block(frame.puts, greeks["puts"], prev[1] if prev else None)
        self._prev_oi = {
            **self._prev_oi,
            name: (
                [int(v) for v in frame.calls.columns["oi"]],
                [int(v) for v in frame.puts.columns["oi"]],
            ),
        }

        header_msg = protocol.market_header(
            underlying=name,
            expiry=header.expiry_date,
            spot_paise=frame.spot_price,
            atm_paise=int(round(metrics.atm * 100)),
            vix_paise=frame.vix,
            risk_free_rate=header.risk_free_rate,
            timestamp_unix_ms=frame.timestamp_unix_ms,
            sequence=frame.sequence,
        )
        grid_msg = protocol.envelope(
            protocol.TYPE_OPTION_GRID,
            {
                "underlying": name,
                "expiry": header.expiry_date,
                "strikes": [_rupees(int(s)) for s in header.strikes],
                "calls": calls_block,
                "puts": puts_block,
                "market_atm": metrics.atm,
                "max_pain": metrics.max_pain,
                "spot_atm": metrics.atm_strike,
                "spot": _rupees(frame.spot_price),
                "vix": _rupees(frame.vix),
            },
        )
        return [header_msg, grid_msg]

    def stock_message(self, ts: int) -> dict:
        matrix = self.stock_matrix
        assert matrix is not None
        frame = matrix.snapshot(ts)  # copy; safe to read
        return self._stock_frame_message(frame)

    def _stock_frame_message(self, frame: StockFrame) -> dict:
        """Full stock board: every captured scalar + all 5 depth levels, per leg.

        COLUMNAR on purpose. A row-per-stock object would repeat ~41 JSON keys 210 times
        per leg; emitting one array per field instead (indexed by stock row) removes that
        duplication entirely. Combined with permessage-deflate this keeps the full L1-L5
        board comfortably small, so the UI never has to fetch depth on demand.

        Column metadata comes from the BIN schema (`is_price` decides paise -> rupees), so
        adding a column to the format automatically streams it.
        """
        legs = {
            "spot": frame.spot,
            "fut_current": frame.fut_current,
            "fut_mid": frame.fut_mid,
            "fut_far": frame.fut_far,
        }
        n = len(self._stock_refs)
        rows = range(n)

        leg_payloads: dict[str, dict] = {}
        for leg_name, leg in legs.items():
            scalars = {
                col.name: (
                    [_rupees(int(leg.scalars[col.name][i])) for i in rows]
                    if col.is_price
                    else [int(leg.scalars[col.name][i]) for i in rows]
                )
                for col in INSTR_SCALAR_COLUMNS
            }
            depth = [
                {
                    col.name: (
                        [_rupees(int(level[col.name][i])) for i in rows]
                        if col.is_price
                        else [int(level[col.name][i]) for i in rows]
                    )
                    for col in DEPTH_LEVEL_COLUMNS
                }
                for level in leg.depth
            ]
            leg_payloads[leg_name] = {"scalars": scalars, "depth": depth}

        return protocol.envelope(
            protocol.TYPE_STOCK_BOARD,
            {
                "timestamp": frame.timestamp_unix_ms,
                "count": n,
                # Static per session, but sent each tick so a late subscriber is complete
                # immediately; they compress to almost nothing after the first frame.
                "tradingsymbols": [ref.tradingsymbol for ref in self._stock_refs],
                "names": [ref.name for ref in self._stock_refs],
                "future_expiries": [list(ref.future_expiries) for ref in self._stock_refs],
                "legs": leg_payloads,
                "live_spread": [
                    live_spread(frame, i) if len(self._stock_refs[i].future_expiries) >= 2 else 0.0
                    for i in rows
                ],
                "daily_spread": [
                    daily_spread(frame, i) if len(self._stock_refs[i].future_expiries) >= 2 else 0.0
                    for i in rows
                ],
            },
        )

    # -- async broadcast --------------------------------------------------- #

    async def broadcast_all(self, ts: int | None = None) -> None:
        ts = ts if ts is not None else self._clock()
        for name, table in self.index_tables.items():
            for msg in self.index_messages(name, table, ts):
                await self.hub.broadcast("market-data", msg)
        if self.stock_matrix is not None:
            await self.hub.broadcast("stocks", self.stock_message(ts))
        if self.monitor is not None:
            await self.hub.broadcast("capture-status", self.monitor.snapshot())
        await self.hub.broadcast("session", protocol.heartbeat(ts))

    def publish_latest(self, snapshot: CaptureSnapshot) -> None:
        """Queue a best-effort display update without delaying capture.

        Only one websocket publish runs at a time. While it is in flight, newer
        timestamps replace the pending one, so a slow frontend cannot create an
        unbounded queue or backpressure the API-ingestion/BIN-writer path.
        """
        self._latest_snapshot = snapshot
        if self._publish_task is None or self._publish_task.done():
            self._publish_task = asyncio.create_task(
                self._drain_latest(), name="capture-ui-publisher"
            )

    async def _drain_latest(self) -> None:
        try:
            while self._latest_snapshot is not None:
                snapshot = self._latest_snapshot
                self._latest_snapshot = None
                try:
                    await self._publish_snapshot(snapshot)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - UI must never stop capture
                    self._log_broadcast_failure(exc)
                await asyncio.sleep(0)
        finally:
            self._publish_task = None
            if self._latest_snapshot is not None:
                self._publish_task = asyncio.create_task(
                    self._drain_latest(), name="capture-ui-publisher"
                )

    def _log_broadcast_failure(self, exc: BaseException) -> None:
        """Log a broadcast failure, throttled to ~once/10s.

        Called from the capture-cadence publish loop, so unconditional logging would
        flood. We keep a running count and emit the exception *type* plus how many
        occurred in the window, so recurring failures stay visible without spamming a
        line every tick. The exception message is deliberately omitted so a broadcast
        payload can never leak into the logs (see test_broadcaster).
        """
        self._broadcast_fail_count += 1
        now = self._clock()
        if (
            self._last_broadcast_fail_log_ms is None
            or (now - self._last_broadcast_fail_log_ms) >= 10_000
        ):
            logger.warning(
                "best-effort frontend broadcast failed: %s (%d occurrence(s) in the last window)",
                type(exc).__name__,
                self._broadcast_fail_count,
            )
            self._last_broadcast_fail_log_ms = now
            self._broadcast_fail_count = 0


    async def _publish_snapshot(self, snapshot: CaptureSnapshot) -> None:
        messages = await asyncio.to_thread(self._build_snapshot_messages, snapshot)
        for topic, message in messages:
            await self.hub.broadcast(topic, message)

    def _build_snapshot_messages(
        self, snapshot: CaptureSnapshot
    ) -> tuple[tuple[str, dict], ...]:
        # LATENCY WINDOW STARTS HERE — immediately before the first Greeks reconstruction
        # — and ends once the whole 1 Hz batch is encoded and ready to hand to the
        # websocket hub. It therefore covers exactly the server-side work: IV/Greeks for
        # every chain, chain metrics, the columnar stock board, and monitor telemetry.
        build_start = time.perf_counter()
        messages: list[tuple[str, dict]] = []
        greeks_start = build_start
        for name, frame in snapshot.index_frames:
            header = self._index_headers.get(name)
            if header is None:
                raise ValueError(f"missing display metadata for index {name}")
            messages.extend(
                ("market-data", message)
                for message in self._index_frame_messages(name, header, frame)
            )
        greeks_ms = (time.perf_counter() - greeks_start) * 1000.0

        if snapshot.stock_frame is not None:
            messages.append(("stocks", self._stock_frame_message(snapshot.stock_frame)))
        stocks_ms = (time.perf_counter() - greeks_start) * 1000.0 - greeks_ms

        if self.monitor is not None:
            status = self.monitor.snapshot()
            messages.append(("capture-status", status))
            self._maybe_persist_snapshot(status.get("payload"))
        messages.append(("session", protocol.heartbeat(snapshot.timestamp_unix_ms)))

        # Batch is ready for the stream: close the window.
        build_ms = (time.perf_counter() - build_start) * 1000.0
        self.last_build_ms = round(build_ms, 2)
        self.last_greeks_ms = round(greeks_ms, 2)
        self.last_stocks_ms = round(stocks_ms, 2)
        # How long the frame waited between its grid tick and the start of this build —
        # useful to separate "the build is slow" from "we were queued behind something".
        self.last_queue_ms = max(0, self._clock() - snapshot.timestamp_unix_ms) - int(build_ms)

        meta = {
            "pipeline_ms": self.last_build_ms,
            "greeks_ms": self.last_greeks_ms,
            "stocks_ms": self.last_stocks_ms,
        }
        for _topic, message in messages:
            existing = message.get("meta")
            if existing is None:
                message["meta"] = dict(meta)
            else:
                existing.update(meta)
        return tuple(messages)

    def _maybe_persist_snapshot(self, payload: dict | None, *, force: bool = False) -> None:
        """Throttled write of the enriched monitor payload to ``_state/stats/``.

        Runs on the display thread (already off the capture path). Persistence is
        best-effort: any failure is logged at debug and never disrupts broadcasting.
        """
        if payload is None or self._stats_state_dir is None or self._trading_date is None:
            return
        now = self._clock()
        if (
            not force
            and self._last_snapshot_write_ms is not None
            and (now - self._last_snapshot_write_ms) < self._snapshot_interval_ms
        ):
            return
        self._last_snapshot_write_ms = now
        try:
            from app.ops import stats_store

            stats_store.write_capture_snapshot(
                self._stats_state_dir, self._trading_date, {**payload, "persisted_at": now}
            )
        except Exception:  # noqa: BLE001 - persistence must never break the UI path
            logger.debug("failed to persist capture snapshot", exc_info=True)

    def persist_snapshot_now(self) -> None:
        """Force an immediate capture-snapshot write (e.g. on capture stop)."""
        if self.monitor is None:
            return
        try:
            payload = self.monitor.snapshot().get("payload")
        except Exception:  # noqa: BLE001
            return
        self._maybe_persist_snapshot(payload, force=True)

    async def wait_until_idle(self) -> None:
        """Wait until the current and coalesced display updates are complete."""
        while self._publish_task is not None:
            await self._publish_task

    async def close(self) -> None:
        """Discard pending display work during capture shutdown."""
        self._latest_snapshot = None
        task = self._publish_task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
