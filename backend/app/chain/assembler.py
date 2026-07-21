"""Assemble a per-index option chain: strike vector + token->role map.

Ported from ``algo_engine`` (``oc_maker/table/assembler.rs``). Produces the fixed
strike vector (paise) that goes in the file header and the O(1) ``token -> role`` map
used to route live ticks. docs/30-live-capture/option-chain-selection.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.chain.config import VIX_TOKEN, IndexConfig
from app.chain.filter import get_spot_atm, option_chain_filter, rupees_to_paise
from app.kite.instruments import Instrument, filter_options

# Role kinds for the token map.
ROLE_OPTION = "option"
ROLE_SPOT = "spot"
ROLE_VIX = "vix"


@dataclass(frozen=True)
class Role:
    """What a subscribed instrument_token represents in the chain."""

    kind: str  # ROLE_OPTION | ROLE_SPOT | ROLE_VIX
    side: str | None = None  # "CE" | "PE" for options
    index: int | None = None  # strike position for options


@dataclass
class OptionChain:
    underlying: str
    expiry: str
    reference_spot_paise: int
    atm_paise: int
    strikes: np.ndarray  # i64 paise, ascending, fixed for the day
    call_tokens: np.ndarray  # u64, aligned to strikes (0 = missing)
    put_tokens: np.ndarray  # u64, aligned to strikes (0 = missing)
    token_map: dict[int, Role]

    @property
    def n_strikes(self) -> int:
        return int(self.strikes.shape[0])


def find_nearest_expiry(options: list[Instrument], today: str) -> str:
    """Nearest expiry >= ``today`` (ISO strings sort chronologically); else earliest."""
    expiries = sorted({o.expiry for o in options if o.expiry})
    if not expiries:
        raise ValueError("no expiries in instrument set")
    future = [e for e in expiries if e >= today]
    return future[0] if future else expiries[0]


def build_option_chain(
    instruments: list[Instrument],
    config: IndexConfig,
    spot: float,
    expiry: str | None = None,
    today: str | None = None,
    strikes_per_side: int = 50,
) -> OptionChain:
    """Build the chain metadata (strike vector + token map) for one index.

    ``spot`` is in rupees and must be > 0. ``expiry`` defaults to the nearest expiry.
    """
    if spot <= 0:
        raise ValueError("spot must be > 0 before assembly")

    underlying_options = filter_options(instruments, config.underlying)
    if not underlying_options:
        raise ValueError(f"instrument master has no options for '{config.underlying}'")

    if expiry is None:
        if today is None:
            raise ValueError("either expiry or today must be provided")
        expiry = find_nearest_expiry(underlying_options, today)

    expiry_options = [o for o in underlying_options if o.expiry == expiry]
    if not expiry_options:
        raise ValueError(f"no contracts for {config.underlying} expiry {expiry}")

    step_paise = config.step * 100
    atm_paise = get_spot_atm(rupees_to_paise(spot), step_paise)
    result = option_chain_filter(expiry_options, atm_paise, strikes_per_side)

    strikes = np.array(result.strikes_paise, dtype="<i8")
    n = strikes.shape[0]
    strike_to_index = {int(s): i for i, s in enumerate(result.strikes_paise)}

    call_tokens = np.zeros(n, dtype="<u8")
    put_tokens = np.zeros(n, dtype="<u8")
    token_map: dict[int, Role] = {}

    for o in result.calls:
        idx = strike_to_index[rupees_to_paise(o.strike)]
        call_tokens[idx] = o.instrument_token
        token_map[o.instrument_token] = Role(ROLE_OPTION, side="CE", index=idx)
    for o in result.puts:
        idx = strike_to_index[rupees_to_paise(o.strike)]
        put_tokens[idx] = o.instrument_token
        token_map[o.instrument_token] = Role(ROLE_OPTION, side="PE", index=idx)

    token_map[config.spot_token] = Role(ROLE_SPOT)
    token_map[VIX_TOKEN] = Role(ROLE_VIX)

    return OptionChain(
        underlying=config.underlying,
        expiry=expiry,
        reference_spot_paise=rupees_to_paise(spot),
        atm_paise=result.effective_atm_paise,
        strikes=strikes,
        call_tokens=call_tokens,
        put_tokens=put_tokens,
        token_map=token_map,
    )
