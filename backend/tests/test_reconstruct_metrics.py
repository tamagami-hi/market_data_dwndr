"""Tests for chain metrics (ATM/max-pain/PCR) and CalSpread spreads."""

from __future__ import annotations

import numpy as np

from app.bin_codec.layout import IndexFrame, InstrColumns, RawBlock, StockFrame
from app.reconstruct.metrics import reconstruct_chain_metrics
from app.reconstruct.spreads import daily_spread, live_spread, spread_summary


def _index_header():
    from app.bin_codec.layout import IndexHeader

    # BANKNIFTY step 100; strikes 100/200/300 rupees (paise x100)
    strikes = np.array([10_000, 20_000, 30_000], dtype="<i8")
    return IndexHeader("2026-07-21", "BANKNIFTY", "2026-07-31", 0.0691, strikes)


def test_chain_metrics_pcr_and_symmetric_max_pain():
    header = _index_header()
    calls = RawBlock.zeros(3)
    puts = RawBlock.zeros(3)
    calls.columns["oi"][:] = [10, 10, 10]
    puts.columns["oi"][:] = [10, 10, 10]
    calls.columns["volume"][:] = [5, 5, 5]
    puts.columns["volume"][:] = [10, 10, 10]

    frame = IndexFrame(1000, 0, 20_500, 0, calls, puts)  # spot 205 rupees
    m = reconstruct_chain_metrics(frame, header)

    assert m.atm == 200.0  # 205 rounds down to 200 (step 100)
    assert m.atm_strike == 200.0
    assert m.max_pain == 200.0  # symmetric OI -> middle strike
    assert m.pcr_oi == 1.0  # 30/30
    assert m.pcr_volume == 2.0  # 30 put vol / 15 call vol


def test_chain_metrics_skewed_max_pain():
    header = _index_header()
    calls = RawBlock.zeros(3)
    puts = RawBlock.zeros(3)
    # Light call OI low, heavy put OI high -> payout minimized at the top strike.
    calls.columns["oi"][:] = [10, 0, 0]
    puts.columns["oi"][:] = [0, 0, 100]
    frame = IndexFrame(1000, 0, 20_000, 0, calls, puts)
    m = reconstruct_chain_metrics(frame, header)
    # payout(300)=10*200=2000 is the minimum vs payout(100)=20000, payout(200)=11000
    assert m.max_pain == 300.0
    assert m.pcr_oi == 10.0  # 100 put / 10 call


def _stock_frame_with_row(ltp_cur, ltp_mid, close_cur, close_mid) -> StockFrame:
    spot = InstrColumns.zeros(1)
    fut_current = InstrColumns.zeros(1)
    fut_mid = InstrColumns.zeros(1)
    fut_far = InstrColumns.zeros(1)
    fut_current.scalars["ltp"][0] = ltp_cur
    fut_mid.scalars["ltp"][0] = ltp_mid
    fut_current.scalars["ohlc_close"][0] = close_cur
    fut_mid.scalars["ohlc_close"][0] = close_mid
    return StockFrame(1000, 0, spot, fut_current, fut_mid, fut_far)


def test_live_and_daily_spread():
    frame = _stock_frame_with_row(246000, 247500, 245000, 246800)  # paise
    assert abs(live_spread(frame, 0) - 15.0) < 1e-9  # 2475.00 - 2460.00
    assert abs(daily_spread(frame, 0) - 18.0) < 1e-9  # 2468.00 - 2450.00


def test_spread_summary_stats():
    s = spread_summary([10.0, 12.0, 8.0, 14.0, 6.0])
    assert s.count == 5
    assert abs(s.mean - 10.0) < 1e-9
    assert s.minimum == 6.0 and s.maximum == 14.0
    assert abs(s.mean_deviation - 2.4) < 1e-9  # mean(|x-10|)=(0+2+2+4+4)/5
    assert 0.0 <= s.mean_reversion_prob <= 1.0
    assert spread_summary([]).count == 0
