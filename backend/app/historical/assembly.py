"""Assemble downloaded candles into ``.bin`` frames (bin_export pattern).

Rows are grouped by ``(trading_date, timestamp)``; per-contract candles are placed into
the aligned calls/puts columns (indices) / stock rows. One header per file, then one
frame per timestamp, ``sequence`` incrementing per date. Historical candles carry only
OHLC + volume + OI, so bid/ask/depth columns stay ``0`` (raw = what the source gives);
Greeks/IV remain unstored (docs/40-historical/historical-data.md).
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.bin_codec.layout import (
    IndexFrame,
    IndexHeader,
    InstrColumns,
    RawBlock,
    StockFrame,
    StockHeader,
    StockRef,
)
from app.bin_codec.writer import IndexBinWriter, StockBinWriter
from app.historical.client import Candle
from app.kite.ticks import to_paise
from app.ops.calendar import TradingCalendar


def _fill_scalar_from_candle(columns: dict, idx: int, candle: Candle) -> None:
    """Write a candle's OHLC/vol/OI into a column set at position ``idx``."""
    columns["ltp"][idx] = to_paise(candle.close)  # candle close == last price
    columns["oi"][idx] = candle.oi
    columns["volume"][idx] = candle.volume
    columns["ohlc_open"][idx] = to_paise(candle.open)
    columns["ohlc_high"][idx] = to_paise(candle.high)
    columns["ohlc_low"][idx] = to_paise(candle.low)
    columns["ohlc_close"][idx] = to_paise(candle.close)


# --------------------------------------------------------------------------- #
# Index history
# --------------------------------------------------------------------------- #


@dataclass
class ContractSeries:
    """One option contract's candles: which side + strike it belongs to."""

    side: str  # "CE" | "PE"
    strike_paise: int
    candles: list[Candle]


def assemble_index_history(
    strikes_paise: list[int],
    contracts: list[ContractSeries],
    calendar: TradingCalendar,
) -> dict[str, list[IndexFrame]]:
    """Group contract candles into per-date, per-timestamp ``IndexFrame`` lists."""
    strike_index = {int(s): i for i, s in enumerate(strikes_paise)}
    n = len(strikes_paise)

    # date -> timestamp -> list of (side, idx, candle)
    by_date: dict[str, dict[int, list[tuple[str, int, Candle]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for contract in contracts:
        if contract.strike_paise not in strike_index:
            continue
        idx = strike_index[contract.strike_paise]
        for candle in contract.candles:
            d = calendar.trading_date(candle.timestamp_unix_ms)
            by_date[d][candle.timestamp_unix_ms].append((contract.side, idx, candle))

    frames_by_date: dict[str, list[IndexFrame]] = {}
    for d in sorted(by_date):
        frames: list[IndexFrame] = []
        for seq, ts in enumerate(sorted(by_date[d])):
            calls = RawBlock.zeros(n)
            puts = RawBlock.zeros(n)
            for side, idx, candle in by_date[d][ts]:
                block = calls if side == "CE" else puts
                _fill_scalar_from_candle(block.columns, idx, candle)
            frames.append(IndexFrame(ts, seq, 0, 0, calls, puts))
        frames_by_date[d] = frames
    return frames_by_date


def write_index_history(
    indices_his_dir: str | os.PathLike[str],
    underlying: str,
    expiry: str,
    risk_free_rate: float,
    strikes_paise: list[int],
    frames_by_date: dict[str, list[IndexFrame]],
) -> list[Path]:
    """Write per-date historical index files under ``INDICES_HIS/<underlying>/``."""
    strikes = np.array(strikes_paise, dtype="<i8")
    written: list[Path] = []
    for trading_date, frames in frames_by_date.items():
        path = Path(indices_his_dir) / underlying / f"{trading_date}.bin"
        header = IndexHeader(trading_date, underlying, expiry, risk_free_rate, strikes)
        with IndexBinWriter(path) as w:
            w.write_header(header)
            for frame in frames:
                w.append_frame(frame)
        written.append(path)
    return written


# --------------------------------------------------------------------------- #
# Stock history
# --------------------------------------------------------------------------- #


@dataclass
class StockContractSeries:
    """One stock leg's candles: which matrix row + leg it belongs to."""

    row: int
    leg: str  # "spot" | "fut_current" | "fut_mid" | "fut_far"
    candles: list[Candle]


def assemble_stock_history(
    n_stocks: int,
    series: list[StockContractSeries],
    calendar: TradingCalendar,
) -> dict[str, list[StockFrame]]:
    """Group stock leg candles into per-date, per-timestamp ``StockFrame`` lists."""
    by_date: dict[str, dict[int, list[tuple[int, str, Candle]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for s in series:
        for candle in s.candles:
            d = calendar.trading_date(candle.timestamp_unix_ms)
            by_date[d][candle.timestamp_unix_ms].append((s.row, s.leg, candle))

    frames_by_date: dict[str, list[StockFrame]] = {}
    for d in sorted(by_date):
        frames: list[StockFrame] = []
        for seq, ts in enumerate(sorted(by_date[d])):
            legs = {leg: InstrColumns.zeros(n_stocks) for leg in
                    ("spot", "fut_current", "fut_mid", "fut_far")}
            for row, leg, candle in by_date[d][ts]:
                _fill_scalar_from_candle(legs[leg].scalars, row, candle)
            frames.append(
                StockFrame(ts, seq, legs["spot"], legs["fut_current"],
                           legs["fut_mid"], legs["fut_far"])
            )
        frames_by_date[d] = frames
    return frames_by_date


def write_stock_history(
    stocks_his_dir: str | os.PathLike[str],
    risk_free_rate: float,
    stock_refs: list[StockRef],
    frames_by_date: dict[str, list[StockFrame]],
) -> list[Path]:
    """Write per-date historical stock files under ``STOCKS_HIS/``."""
    written: list[Path] = []
    for trading_date, frames in frames_by_date.items():
        path = Path(stocks_his_dir) / f"{trading_date}.bin"
        header = StockHeader(trading_date, risk_free_rate, stock_refs)
        with StockBinWriter(path) as w:
            w.write_header(header)
            for frame in frames:
                w.append_frame(frame)
        written.append(path)
    return written
