"""Tests for the frontend broadcaster (Greeks-enriched OptionGrid + StockBoard)."""

from __future__ import annotations

import asyncio
import time
from types import MethodType

from app.capture.broadcaster import Broadcaster
from app.capture.engine import CaptureEngine
from app.capture.snapshot import CaptureSnapshot
from app.chain.assembler import build_option_chain
from app.chain.config import get_index_config
from app.chain.table import IndexTable
from app.stocks.board import build_board
from app.stocks.depth import stock_depth_snapshot
from app.stocks.matrix import StockMatrix
from app.ws.protocol import TYPE_MARKET_HEADER, TYPE_OPTION_GRID, TYPE_STOCK_BOARD
from tests.test_board import _sample_instruments
from tests.test_chain import _make_options
from tests.test_table_matrix import _depth5, _full_tick


class FakeHub:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, topic: str, message: dict) -> int:
        self.sent.append((topic, message))
        return 1


def _empty_snapshot(timestamp: int) -> CaptureSnapshot:
    return CaptureSnapshot(timestamp, (), None)


class _FakeMonitor:
    def __init__(self, payload):
        self._payload = payload

    def snapshot(self):
        return {"type": "CaptureStatus", "payload": self._payload}


def test_broadcaster_persists_capture_snapshot(tmp_path):
    from app.ops import stats_store

    payload = {"per_underlying": [], "global": {"fps": 1.0}}
    bc = Broadcaster(
        {},
        None,
        FakeHub(),
        monitor=_FakeMonitor(payload),
        stats_state_dir=tmp_path,
        trading_date="2026-07-21",
    )
    bc.persist_snapshot_now()
    saved = stats_store.load_capture_snapshot(tmp_path, "2026-07-21")
    assert saved is not None
    assert saved["global"]["fps"] == 1.0
    assert "persisted_at" in saved


def test_broadcaster_snapshot_write_is_throttled(tmp_path):
    payload = {"per_underlying": [], "global": {}}
    clock = {"t": 1_000_000}
    bc = Broadcaster(
        {},
        None,
        FakeHub(),
        monitor=_FakeMonitor(payload),
        clock=lambda: clock["t"],
        stats_state_dir=tmp_path,
        trading_date="2026-07-21",
        snapshot_interval_ms=60_000,
    )
    bc._maybe_persist_snapshot(payload)  # first write
    first = bc._last_snapshot_write_ms
    clock["t"] += 1_000  # only 1s later -> throttled, no write
    bc._maybe_persist_snapshot(payload)
    assert bc._last_snapshot_write_ms == first
    clock["t"] += 60_000  # past the interval -> writes again
    bc._maybe_persist_snapshot(payload)
    assert bc._last_snapshot_write_ms == first + 61_000


def _nifty_table():
    options = _make_options("NIFTY", "2026-07-31", list(range(24000, 25001, 50)))
    chain = build_option_chain(
        options, get_index_config("NIFTY"), spot=24500.0, expiry="2026-07-31"
    )
    return IndexTable(chain, 0.0691, "2026-07-21")


def test_index_messages_include_greeks_and_metrics():
    table = _nifty_table()
    # feed a CE + spot + vix so IV/greeks reconstruct
    ce_token = int(table.chain.call_tokens[0])
    table.apply_tick(_full_tick(ce_token, 120.0, oi=500))
    table.apply_tick(_full_tick(get_index_config("NIFTY").spot_token, 24500.0))
    from app.chain.config import VIX_TOKEN

    table.apply_tick(_full_tick(VIX_TOKEN, 13.5))

    bc = Broadcaster({"NIFTY": table}, None, FakeHub())
    msgs = bc.index_messages("NIFTY", table, ts=1_753_070_400_000)
    assert [m["type"] for m in msgs] == [TYPE_MARKET_HEADER, TYPE_OPTION_GRID]

    header = msgs[0]["payload"]
    assert header["underlying"] == "NIFTY"
    assert header["spot"] == 24500.0
    assert abs(header["vix"] - 13.5) < 1e-9
    assert header["risk_free_rate"] == table.risk_free_rate

    grid = msgs[1]["payload"]
    assert len(grid["strikes"]) == table.chain.n_strikes
    # block carries the full column set the frontend renders
    for col in ("oi", "change_in_oi", "volume", "iv", "delta", "gamma", "theta",
                "vega", "rho", "bid", "ask", "ltp", "change"):
        assert col in grid["calls"] and col in grid["puts"]
    assert "market_atm" in grid and "max_pain" in grid and "spot_atm" in grid


def test_change_in_oi_is_delta_between_broadcasts():
    table = _nifty_table()
    ce_token = int(table.chain.call_tokens[0])
    bc = Broadcaster({"NIFTY": table}, None, FakeHub())

    table.apply_tick(_full_tick(ce_token, 120.0, oi=500))
    first = bc.index_messages("NIFTY", table, ts=1000)[1]["payload"]
    assert first["calls"]["change_in_oi"][0] == 0  # first broadcast -> baseline

    table.apply_tick(_full_tick(ce_token, 121.0, oi=650))
    second = bc.index_messages("NIFTY", table, ts=2000)[1]["payload"]
    assert second["calls"]["change_in_oi"][0] == 150  # 650 - 500


def test_stock_message_shape():
    nfo, nse = _sample_instruments()
    matrix = StockMatrix(build_board(nfo, nse), 0.0691, "2026-07-21")
    matrix.apply_tick(
        _full_tick(
            519937,
            2950.5,
            oi=1000,
            buy=_depth5(2950.45),
            sell=_depth5(2950.55),
        )
    )  # M&M spot
    matrix.apply_tick(
        _full_tick(
            1001,
            2460.0,
            oi=8000,
            buy=_depth5(2459.90),
            sell=_depth5(2460.10),
        )
    )  # RELIANCE fut_current
    matrix.apply_tick(_full_tick(1002, 2475.0, oi=6000))  # RELIANCE fut_mid

    bc = Broadcaster({}, matrix, FakeHub())
    msg = bc.stock_message(ts=1000)
    assert msg["type"] == TYPE_STOCK_BOARD
    stocks = {s["name"]: s for s in msg["payload"]["stocks"]}
    assert set(stocks) == {"M&M", "RELIANCE"}
    reliance = stocks["RELIANCE"]
    assert reliance["futures"][0]["ltp"] == 2460.0
    assert "depth" not in reliance["futures"][0]
    assert "spot_depth" not in stocks["M&M"]

    depth = stock_depth_snapshot(matrix, "RELIANCE")
    assert depth is not None
    assert len(depth["futures"][0]["depth"]) == 5
    assert depth["futures"][0]["depth"][0] == {
        "level": 1,
        "bid_price": 2459.9,
        "bid_qty": 100,
        "bid_orders": 1,
        "ask_price": 2460.1,
        "ask_qty": 100,
        "ask_orders": 1,
    }
    assert depth["futures"][0]["depth"][4]["bid_price"] == 2459.7
    assert depth["futures"][0]["depth"][4]["ask_orders"] == 5
    assert len(depth["futures"][1]["depth"]) == 5
    assert depth["futures"][1]["depth"][0]["bid_price"] == 0.0
    mm_depth = stock_depth_snapshot(matrix, "M&M")
    assert mm_depth is not None
    assert len(mm_depth["spot_depth"]) == 5
    assert mm_depth["spot_depth"][0]["ask_price"] == 2950.55
    # live spread = fut_mid - fut_current = 2475 - 2460 = 15
    assert abs(reliance["live_spread"] - 15.0) < 1e-9


async def test_broadcast_all_pushes_all_topics():
    table = _nifty_table()
    nfo, nse = _sample_instruments()
    matrix = StockMatrix(build_board(nfo, nse), 0.0691, "2026-07-21")
    hub = FakeHub()

    class FakeMonitor:
        def snapshot(self):
            return {"type": "CaptureStatus", "payload": {"per_underlying": [], "global": {}}}

    bc = Broadcaster({"NIFTY": table}, matrix, hub, monitor=FakeMonitor())
    await bc.broadcast_all(ts=1000)

    topics = [t for t, _ in hub.sent]
    assert "market-data" in topics  # header + grid
    assert "stocks" in topics
    assert "capture-status" in topics
    assert "session" in topics  # heartbeat


async def test_publish_latest_is_non_blocking_and_coalesces_stale_frames():
    """A slow UI send keeps only the newest pending display timestamp."""
    broadcaster = Broadcaster({}, None, FakeHub())
    first_send_started = asyncio.Event()
    release_first_send = asyncio.Event()
    sent_timestamps: list[int] = []

    async def slow_publish(self, snapshot: CaptureSnapshot) -> None:
        sent_timestamps.append(snapshot.timestamp_unix_ms)
        if len(sent_timestamps) == 1:
            first_send_started.set()
            await release_first_send.wait()

    broadcaster._publish_snapshot = MethodType(slow_publish, broadcaster)

    broadcaster.publish_latest(_empty_snapshot(1000))
    await asyncio.wait_for(first_send_started.wait(), timeout=0.2)

    # Neither call waits for the in-flight websocket send. The older pending
    # frame is replaced because display freshness matters more than completeness.
    broadcaster.publish_latest(_empty_snapshot(2000))
    broadcaster.publish_latest(_empty_snapshot(3000))
    assert sent_timestamps == [1000]

    release_first_send.set()
    await asyncio.wait_for(broadcaster.wait_until_idle(), timeout=0.2)
    assert sent_timestamps == [1000, 3000]


async def test_publish_latest_contains_broadcast_failures(caplog):
    broadcaster = Broadcaster({}, None, FakeHub())

    async def failing_publish(self, snapshot: CaptureSnapshot) -> None:
        raise RuntimeError("frontend unavailable")

    broadcaster._publish_snapshot = MethodType(failing_publish, broadcaster)
    broadcaster.publish_latest(_empty_snapshot(1000))

    await asyncio.wait_for(broadcaster.wait_until_idle(), timeout=0.2)
    assert "frontend unavailable" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_slow_display_construction_does_not_block_capture_event_loop():
    broadcaster = Broadcaster({}, None, FakeHub())

    def cpu_heavy_build(self, snapshot: CaptureSnapshot) -> tuple:
        time.sleep(0.1)
        return ()

    broadcaster._build_snapshot_messages = MethodType(cpu_heavy_build, broadcaster)

    started_at = time.monotonic()
    broadcaster.publish_latest(_empty_snapshot(1000))
    await asyncio.sleep(0.01)
    elapsed = time.monotonic() - started_at
    await broadcaster.close()

    assert elapsed < 0.05


def test_display_worker_uses_copied_capture_frame_not_mutable_live_table():
    table = _nifty_table()
    spot_token = get_index_config("NIFTY").spot_token
    table.apply_tick(_full_tick(spot_token, 24500.0))
    engine = CaptureEngine({"NIFTY": table}, None, {}, None)
    snapshot = engine.capture_snapshot(1000)

    # A subsequent API tick mutates the live table after the capture hand-off.
    table.apply_tick(_full_tick(spot_token, 25000.0))

    broadcaster = Broadcaster({"NIFTY": table}, None, FakeHub())
    messages = broadcaster._build_snapshot_messages(snapshot)
    market_header = next(message for _, message in messages if message["type"] == "MarketHeader")
    assert market_header["payload"]["spot"] == 24500.0
