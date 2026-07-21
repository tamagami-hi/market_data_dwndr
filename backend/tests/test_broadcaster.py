"""Tests for the frontend broadcaster (Greeks-enriched OptionGrid + StockBoard)."""

from __future__ import annotations

from app.capture.broadcaster import Broadcaster
from app.chain.assembler import build_option_chain
from app.chain.config import get_index_config
from app.chain.table import IndexTable
from app.stocks.board import build_board
from app.stocks.matrix import StockMatrix
from app.ws.protocol import TYPE_MARKET_HEADER, TYPE_OPTION_GRID, TYPE_STOCK_BOARD
from tests.test_board import _sample_instruments
from tests.test_chain import _make_options
from tests.test_table_matrix import _full_tick


class FakeHub:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def broadcast(self, topic: str, message: dict) -> int:
        self.sent.append((topic, message))
        return 1


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
    matrix.apply_tick(_full_tick(519937, 2950.5, oi=1000))  # M&M spot
    matrix.apply_tick(_full_tick(1001, 2460.0, oi=8000))  # RELIANCE fut_current
    matrix.apply_tick(_full_tick(1002, 2475.0, oi=6000))  # RELIANCE fut_mid

    bc = Broadcaster({}, matrix, FakeHub())
    msg = bc.stock_message(ts=1000)
    assert msg["type"] == TYPE_STOCK_BOARD
    stocks = {s["name"]: s for s in msg["payload"]["stocks"]}
    assert set(stocks) == {"M&M", "RELIANCE"}
    reliance = stocks["RELIANCE"]
    assert reliance["futures"][0]["ltp"] == 2460.0
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
