"""Tests for historical frame assembly -> INDICES_HIS / STOCKS_HIS."""

from __future__ import annotations

from datetime import datetime

from app.bin_codec.layout import FutureRef, StockRef
from app.bin_codec.reader import IndexBinReader, StockBinReader
from app.historical.assembly import (
    ContractSeries,
    StockContractSeries,
    assemble_index_history,
    assemble_stock_history,
    write_index_history,
    write_stock_history,
)
from app.historical.client import Candle
from app.ops.calendar import IST_FALLBACK, TradingCalendar


def _ms(h, mi) -> int:
    return int(datetime(2026, 7, 21, h, mi, tzinfo=IST_FALLBACK).timestamp() * 1000)


def _candle(ts, close, oi=0, vol=0) -> Candle:
    return Candle(ts, open=close, high=close + 1, low=close - 1, close=close, volume=vol, oi=oi)


def test_assemble_and_write_index_history(tmp_path):
    strikes = [2_450_000, 2_460_000]  # paise
    cal = TradingCalendar()
    contracts = [
        ContractSeries("CE", 2_450_000, [_candle(_ms(9, 15), 120.0, oi=500, vol=10),
                                         _candle(_ms(9, 16), 121.0, oi=510, vol=12)]),
        ContractSeries("PE", 2_460_000, [_candle(_ms(9, 15), 88.0, oi=700)]),
    ]
    frames_by_date = assemble_index_history(strikes, contracts, cal)
    assert list(frames_by_date) == ["2026-07-21"]
    frames = frames_by_date["2026-07-21"]
    assert len(frames) == 2
    # frame 0 (09:15): CE@idx0 filled, PE@idx1 filled
    assert frames[0].calls.columns["ltp"][0] == 12000  # 120.00 -> paise
    assert frames[0].calls.columns["oi"][0] == 500
    assert frames[0].puts.columns["ltp"][1] == 8800
    assert frames[0].sequence == 0
    # frame 1 (09:16): only CE updated, PE stays 0
    assert frames[1].calls.columns["ltp"][0] == 12100
    assert frames[1].puts.columns["ltp"][1] == 0
    assert frames[1].sequence == 1

    paths = write_index_history(tmp_path, "NIFTY", "2026-07-24", 0.0691, strikes, frames_by_date)
    assert len(paths) == 1
    with IndexBinReader(paths[0]) as r:
        assert len(r) == 2
        assert r.header().underlying == "NIFTY"
        assert r.header().risk_free_rate == 0.0691
        assert r.frame(0).calls.columns["ohlc_high"][0] == 12100  # 121.00 high -> paise


def test_assemble_and_write_stock_history(tmp_path):
    cal = TradingCalendar()
    refs = [
        StockRef("RELIANCE", "RELIANCE", 738561, 250,
                 [FutureRef(1001, "2026-07-31", 250)]),
    ]
    series = [
        StockContractSeries(0, "spot", [_candle(_ms(9, 15), 2950.0, oi=0, vol=100)]),
        StockContractSeries(0, "fut_current", [_candle(_ms(9, 15), 2960.0, oi=8000)]),
    ]
    frames_by_date = assemble_stock_history(len(refs), series, cal)
    paths = write_stock_history(tmp_path, 0.0691, refs, frames_by_date)
    assert len(paths) == 1
    with StockBinReader(paths[0]) as r:
        assert len(r) == 1
        frame = r.frame(0)
        assert frame.spot.scalars["ltp"][0] == 295000
        assert frame.fut_current.scalars["ltp"][0] == 296000
        assert frame.fut_current.scalars["oi"][0] == 8000
        # depth columns are zero for historical candles
        assert frame.spot.depth[0]["bid_price"][0] == 0
