"""ATM computation and the ATM +/- 50 strike-window filter.

Ported from ``algo_engine`` (``oc_maker/table/filter.rs``). Strikes are handled as
**integer paise keys** so lookups are exact (no float-key instability).
docs/30-live-capture/option-chain-selection.md.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from app.kite.instruments import Instrument

# --------------------------------------------------------------------------- #


def rupees_to_paise(value: float) -> int:
    """Round a rupees value to integer paise."""
    return int(round(value * 100))


def get_spot_atm(spot: float, step: int) -> int:
    """Round ``spot`` to the nearest ``step`` (both in the same unit).

    Mirrors algo_engine: base to the step below, then round up iff the remainder is
    at least half a step.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    s = int(math.floor(spot))
    base = (s // step) * step
    remainder = s % step
    return base + (0 if remainder < step / 2 else step)


def nearest_index(sorted_values: list[int], target: int) -> int:
    """Index of the value in ``sorted_values`` closest to ``target``."""
    if not sorted_values:
        raise ValueError("empty strike list")
    pos = bisect.bisect_left(sorted_values, target)
    if pos == 0:
        return 0
    if pos >= len(sorted_values):
        return len(sorted_values) - 1
    before = sorted_values[pos - 1]
    after = sorted_values[pos]
    return pos if (after - target) < (target - before) else pos - 1


@dataclass
class FilterResult:
    calls: list[Instrument]
    puts: list[Instrument]
    strikes_paise: list[int]  # windowed strikes, ascending
    effective_atm_paise: int


def option_chain_filter(
    options: list[Instrument],
    atm_paise: int,
    strikes_per_side: int = 50,
) -> FilterResult:
    """Select the ATM +/- ``strikes_per_side`` window from CE/PE ``options``.

    ``options`` should already be restricted to one ``(underlying, expiry)``.
    """
    strike_keys = sorted({rupees_to_paise(o.strike) for o in options})
    if not strike_keys:
        raise ValueError("empty strike list")  # hard guard (algo_engine parity)

    atm_idx = nearest_index(strike_keys, atm_paise)
    start = max(0, atm_idx - strikes_per_side)
    end = min(len(strike_keys) - 1, atm_idx + strikes_per_side)
    window = strike_keys[start : end + 1]
    window_set = set(window)

    def in_window(o: Instrument, side: str) -> bool:
        return o.instrument_type == side and rupees_to_paise(o.strike) in window_set

    calls = [o for o in options if in_window(o, "CE")]
    puts = [o for o in options if in_window(o, "PE")]
    return FilterResult(
        calls=calls,
        puts=puts,
        strikes_paise=window,
        effective_atm_paise=strike_keys[atm_idx],
    )
