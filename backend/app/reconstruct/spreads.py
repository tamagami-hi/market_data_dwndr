"""CalSpread stock spread reconstruction + summary statistics.

Spreads and their stats are recomputed from stored raw legs (nearest two futures),
mirroring CalSpread's ``recomputeSpreadSummary`` (docs/30-live-capture/stocks-capture.md,
docs/20-data-and-storage/reconstruction.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.bin_codec.layout import StockFrame


def _paise(v) -> float:
    return float(v) / 100.0


def live_spread(frame: StockFrame, row: int) -> float:
    """Live/hourly spread = fut_mid.ltp - fut_current.ltp (rupees)."""
    return _paise(frame.fut_mid.scalars["ltp"][row]) - _paise(
        frame.fut_current.scalars["ltp"][row]
    )


def daily_spread(frame: StockFrame, row: int) -> float:
    """Daily spread = fut_mid.close - fut_current.close (rupees)."""
    return _paise(frame.fut_mid.scalars["ohlc_close"][row]) - _paise(
        frame.fut_current.scalars["ohlc_close"][row]
    )


@dataclass(frozen=True)
class SpreadSummary:
    count: int
    mean: float
    minimum: float
    maximum: float
    mean_deviation: float
    std_dev: float
    p95: float
    mean_reversion_prob: float


def spread_summary(daily_closes: list[float]) -> SpreadSummary:
    """Summary stats over a symbol's stored daily spread closes."""
    arr = np.asarray(daily_closes, dtype=np.float64)
    n = arr.size
    if n == 0:
        return SpreadSummary(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    mean = float(arr.mean())
    mean_dev = float(np.abs(arr - mean).mean())
    std = float(arr.std())  # population std
    p95 = float(np.percentile(arr, 95))

    # Mean-reversion probability: fraction of steps moving toward the mean.
    if n >= 2:
        dist = np.abs(arr - mean)
        toward = dist[1:] < dist[:-1]
        mean_reversion_prob = float(toward.mean())
    else:
        mean_reversion_prob = 0.0

    return SpreadSummary(
        count=n,
        mean=mean,
        minimum=float(arr.min()),
        maximum=float(arr.max()),
        mean_deviation=mean_dev,
        std_dev=std,
        p95=p95,
        mean_reversion_prob=mean_reversion_prob,
    )
