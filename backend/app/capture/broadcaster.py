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
from dataclasses import dataclass

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock, StockFrame
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
    return 0.0 if (x is None or math.isnan(x)) else round(float(x), decimals)


def _build_grid_block(raw: RawBlock, greeks_side: dict, prev_oi: list[int] | None) -> dict:
    n = raw.length()
    oi = [int(v) for v in raw.columns["oi"]]
    change_in_oi = [oi[i] - (prev_oi[i] if prev_oi else oi[i]) for i in range(n)]
    return {
        "oi": oi,
        "change_in_oi": change_in_oi,
        "volume": [int(v) for v in raw.columns["volume"]],
        "iv": [_finite(v * 100, 4) if not math.isnan(v) else 0.0 for v in greeks_side["iv"]],
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
    ) -> None:
        self.index_tables = index_tables
        self.stock_matrix = stock_matrix
        self.hub = hub
        self.monitor = monitor
        self._clock = clock
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
        rows = []
        for row, ref in enumerate(self._stock_refs):
            legs = {
                "spot": frame.spot,
                "fut_current": frame.fut_current,
                "fut_mid": frame.fut_mid,
                "fut_far": frame.fut_far,
            }
            futures = []
            leg_names = ["fut_current", "fut_mid", "fut_far"]
            for i, expiry in enumerate(ref.future_expiries):
                leg = legs[leg_names[i]]
                futures.append(
                    {
                        "expiry": expiry,
                        "ltp": _rupees(leg.scalars["ltp"][row]),
                        "oi": int(leg.scalars["oi"][row]),
                    }
                )
            rows.append(
                {
                    "tradingsymbol": ref.tradingsymbol,
                    "name": ref.name,
                    "spot_ltp": _rupees(frame.spot.scalars["ltp"][row]),
                    "futures": futures,
                    "live_spread": (
                        live_spread(frame, row) if len(ref.future_expiries) >= 2 else 0.0
                    ),
                    "daily_spread": (
                        daily_spread(frame, row) if len(ref.future_expiries) >= 2 else 0.0
                    ),
                }
            )
        return protocol.envelope(
            protocol.TYPE_STOCK_BOARD,
            {"timestamp": frame.timestamp_unix_ms, "stocks": rows},
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
                    logger.warning(
                        "best-effort frontend broadcast failed (%s)",
                        type(exc).__name__,
                    )
                await asyncio.sleep(0)
        finally:
            self._publish_task = None
            if self._latest_snapshot is not None:
                self._publish_task = asyncio.create_task(
                    self._drain_latest(), name="capture-ui-publisher"
                )

    async def _publish_snapshot(self, snapshot: CaptureSnapshot) -> None:
        messages = await asyncio.to_thread(self._build_snapshot_messages, snapshot)
        for topic, message in messages:
            await self.hub.broadcast(topic, message)

    def _build_snapshot_messages(
        self, snapshot: CaptureSnapshot
    ) -> tuple[tuple[str, dict], ...]:
        messages: list[tuple[str, dict]] = []
        for name, frame in snapshot.index_frames:
            header = self._index_headers.get(name)
            if header is None:
                raise ValueError(f"missing display metadata for index {name}")
            messages.extend(
                ("market-data", message)
                for message in self._index_frame_messages(name, header, frame)
            )
        if snapshot.stock_frame is not None:
            messages.append(("stocks", self._stock_frame_message(snapshot.stock_frame)))
        if self.monitor is not None:
            messages.append(("capture-status", self.monitor.snapshot()))
        messages.append(("session", protocol.heartbeat(snapshot.timestamp_unix_ms)))
        return tuple(messages)

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
