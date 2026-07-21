"""Tests for the tagged-envelope WebSocket protocol builders."""

from __future__ import annotations

import numpy as np

from app.bin_codec.layout import IndexFrame, RawBlock
from app.ws import protocol


def _block(n: int) -> RawBlock:
    return RawBlock.zeros(n)


def test_envelope_shape():
    msg = protocol.envelope("Foo", {"a": 1})
    assert msg == {"type": "Foo", "payload": {"a": 1}}


def test_grid_block_converts_prices_to_rupees():
    block = _block(3)
    block.columns["ltp"][:] = [12345, 0, 999]  # paise
    block.columns["oi"][:] = [100, 200, 300]
    block.columns["bid"][:] = [12340, 0, 0]
    gb = protocol.grid_block(block)
    assert gb["ltp"] == [123.45, 0.0, 9.99]  # rupees
    assert gb["oi"] == [100, 200, 300]  # ints, unchanged
    assert gb["bid"][0] == 123.40


def test_grid_block_subset_indices():
    block = _block(5)
    block.columns["ltp"][:] = [100, 200, 300, 400, 500]
    gb = protocol.grid_block(block, indices=[1, 3])
    assert gb["ltp"] == [2.0, 4.0]


def test_market_header_rupees():
    msg = protocol.market_header("NIFTY", "2026-07-31", 2456700, 2455000, 1234, 111, 7)
    p = msg["payload"]
    assert msg["type"] == protocol.TYPE_MARKET_HEADER
    assert p["spot"] == 24567.0
    assert p["atm"] == 24550.0
    assert p["vix"] == 12.34
    assert p["sequence"] == 7


def test_option_grid_keyframe():
    strikes = np.array([2450000, 2455000, 2460000], dtype="<i8")
    msg = protocol.option_grid("NIFTY", "2026-07-31", strikes, _block(3), _block(3))
    assert msg["type"] == protocol.TYPE_OPTION_GRID
    assert msg["payload"]["strikes"] == [24500.0, 24550.0, 24600.0]
    assert len(msg["payload"]["calls"]["ltp"]) == 3


def test_option_grid_delta_reports_only_changed_strikes():
    prev_calls, prev_puts = _block(4), _block(4)
    curr_calls, curr_puts = _block(4), _block(4)
    # change LTP at call index 2, and OI at put index 0
    curr_calls.columns["ltp"][2] = 15000
    curr_puts.columns["oi"][0] = 999

    prev = IndexFrame(1000, 0, 0, 0, prev_calls, prev_puts)
    curr = IndexFrame(2000, 1, 0, 0, curr_calls, curr_puts)
    msg = protocol.option_grid_delta("NIFTY", prev, curr)

    assert msg["type"] == protocol.TYPE_OPTION_GRID_DELTA
    assert msg["payload"]["changed_indices"] == [0, 2]
    assert msg["payload"]["sequence"] == 1
    # arrays align to changed_indices order
    assert msg["payload"]["calls"]["ltp"] == [0.0, 150.0]
    assert msg["payload"]["puts"]["oi"] == [999, 0]


def test_capture_status_and_heartbeat():
    cs = protocol.capture_status([{"underlying": "NIFTY"}], {"tokens": 1600})
    assert cs["type"] == protocol.TYPE_CAPTURE_STATUS
    assert cs["payload"]["global"]["tokens"] == 1600
    hb = protocol.heartbeat(12345)
    assert hb == {"type": "Heartbeat", "payload": {"ts": 12345}}
