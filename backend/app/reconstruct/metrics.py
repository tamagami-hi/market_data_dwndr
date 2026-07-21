"""Aggregate option-chain metrics (display only): ATM, max-pain, PCR.

Pure aggregations over stored integer columns -- no IV needed
(docs/20-data-and-storage/reconstruction.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.bin_codec.layout import IndexFrame, IndexHeader
from app.chain.config import get_index_config
from app.chain.filter import get_spot_atm, nearest_index


@dataclass(frozen=True)
class ChainMetrics:
    atm: float  # rounded ATM (rupees)
    atm_strike: float  # nearest listed strike (rupees)
    max_pain: float  # max-pain strike (rupees)
    pcr_oi: float
    pcr_volume: float


def reconstruct_chain_metrics(frame: IndexFrame, header: IndexHeader) -> ChainMetrics:
    strikes_paise = np.asarray(header.strikes, dtype=np.int64)
    strikes = strikes_paise / 100.0

    step_paise = get_index_config(header.underlying).step * 100
    atm_paise = get_spot_atm(int(frame.spot_price), step_paise)
    atm_idx = nearest_index([int(s) for s in strikes_paise], atm_paise)

    call_oi = frame.calls.columns["oi"].astype(np.float64)
    put_oi = frame.puts.columns["oi"].astype(np.float64)
    call_vol = frame.calls.columns["volume"].astype(np.float64)
    put_vol = frame.puts.columns["volume"].astype(np.float64)

    # Max pain: strike K minimizing total in-the-money payout to option holders.
    # payout(K) = sum_i call_oi_i * max(K - Ki, 0) + put_oi_i * max(Ki - K, 0)
    diff = strikes[:, None] - strikes[None, :]  # rows: candidate K, cols: strike_i
    call_pay = np.where(diff > 0, diff, 0.0) @ call_oi
    put_pay = np.where(-diff > 0, -diff, 0.0) @ put_oi
    total = call_pay + put_pay
    max_pain_idx = int(np.argmin(total)) if strikes.size else 0

    total_call_oi = float(call_oi.sum())
    total_put_oi = float(put_oi.sum())
    total_call_vol = float(call_vol.sum())
    total_put_vol = float(put_vol.sum())

    return ChainMetrics(
        atm=atm_paise / 100.0,
        atm_strike=float(strikes[atm_idx]) if strikes.size else 0.0,
        max_pain=float(strikes[max_pain_idx]) if strikes.size else 0.0,
        pcr_oi=(total_put_oi / total_call_oi) if total_call_oi else 0.0,
        pcr_volume=(total_put_vol / total_call_vol) if total_call_vol else 0.0,
    )
