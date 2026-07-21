"""Tests for ATM computation, the ATM +/- 50 filter, and chain assembly."""

from __future__ import annotations

import numpy as np
import pytest

from app.chain.assembler import ROLE_SPOT, ROLE_VIX, build_option_chain, find_nearest_expiry
from app.chain.config import VIX_TOKEN, get_index_config
from app.chain.filter import get_spot_atm, nearest_index, option_chain_filter, rupees_to_paise
from app.kite.instruments import Instrument


def _make_options(
    name: str,
    expiry: str,
    strikes_rupees: list[int],
    base_token: int = 100000,
) -> list[Instrument]:
    """Build CE+PE instruments for the given strikes."""
    out = []
    tok = base_token
    for strike in strikes_rupees:
        for typ in ("CE", "PE"):
            out.append(
                Instrument(
                    instrument_token=tok,
                    exchange_token=tok,
                    tradingsymbol=f"{name}{expiry}{strike}{typ}",
                    name=name,
                    last_price=0.0,
                    expiry=expiry,
                    strike=float(strike),
                    tick_size=0.05,
                    lot_size=50,
                    instrument_type=typ,
                    segment="NFO-OPT",
                    exchange="NFO",
                )
            )
            tok += 1
    return out


# --- get_spot_atm -------------------------------------------------------------


@pytest.mark.parametrize(
    "spot,step,expected",
    [
        (24567, 50, 24550),  # remainder 17 < 25 -> round down
        (24580, 50, 24600),  # remainder 30 >= 25 -> round up
        (24575, 50, 24600),  # exactly half -> round up
        (52340, 100, 52300),
        (52360, 100, 52400),
    ],
)
def test_get_spot_atm(spot, step, expected):
    assert get_spot_atm(spot, step) == expected


def test_get_spot_atm_rejects_bad_step():
    with pytest.raises(ValueError):
        get_spot_atm(100, 0)


def test_nearest_index_fallback():
    strikes = [100, 200, 300, 400]
    assert nearest_index(strikes, 260) == 2  # closer to 300
    assert nearest_index(strikes, 240) == 1  # closer to 200
    assert nearest_index(strikes, 50) == 0  # below range
    assert nearest_index(strikes, 9999) == 3  # above range


# --- option_chain_filter ------------------------------------------------------


def test_filter_selects_exactly_atm_plus_minus_50():
    strikes = list(range(20000, 30001, 50))  # 201 strikes
    options = _make_options("NIFTY", "2026-07-31", strikes)
    result = option_chain_filter(options, rupees_to_paise(24550), strikes_per_side=50)
    assert len(result.strikes_paise) == 101  # ATM +/- 50 = 101
    assert result.effective_atm_paise == rupees_to_paise(24550)
    assert result.strikes_paise[0] == rupees_to_paise(24550 - 50 * 50)
    assert result.strikes_paise[-1] == rupees_to_paise(24550 + 50 * 50)
    # only in-window contracts kept, both sides
    assert len(result.calls) == 101
    assert len(result.puts) == 101


def test_filter_clamps_near_edges():
    strikes = list(range(24000, 24501, 50))  # 11 strikes, small set
    options = _make_options("NIFTY", "2026-07-31", strikes)
    result = option_chain_filter(options, rupees_to_paise(24000), strikes_per_side=50)
    assert len(result.strikes_paise) == 11  # fewer than 101 available


def test_filter_empty_strikes_raises():
    with pytest.raises(ValueError):
        option_chain_filter([], rupees_to_paise(24550))


# --- assembler ----------------------------------------------------------------


def test_find_nearest_expiry():
    options = _make_options("NIFTY", "2026-07-31", [24500]) + _make_options(
        "NIFTY", "2026-08-28", [24500], base_token=200000
    )
    assert find_nearest_expiry(options, "2026-07-21") == "2026-07-31"
    assert find_nearest_expiry(options, "2026-08-01") == "2026-08-28"
    assert find_nearest_expiry(options, "2099-01-01") == "2026-07-31"  # fallback earliest


def test_build_option_chain_token_map_and_strikes():
    strikes = list(range(20000, 30001, 50))  # 201 strikes
    options = _make_options("NIFTY", "2026-07-31", strikes)
    config = get_index_config("NIFTY")

    chain = build_option_chain(options, config, spot=24567.0, expiry="2026-07-31")
    assert chain.n_strikes == 101
    assert chain.strikes.dtype == np.dtype("<i8")
    assert chain.atm_paise == rupees_to_paise(24550)

    # aligned token arrays fully populated
    assert np.count_nonzero(chain.call_tokens) == 101
    assert np.count_nonzero(chain.put_tokens) == 101

    # token map contains spot + VIX roles
    assert chain.token_map[config.spot_token].kind == ROLE_SPOT
    assert chain.token_map[VIX_TOKEN].kind == ROLE_VIX
    # an option token maps to the correct side + strike index
    first_call_token = int(chain.call_tokens[0])
    role = chain.token_map[first_call_token]
    assert role.kind == "option" and role.side == "CE" and role.index == 0


def test_build_option_chain_rejects_nonpositive_spot():
    options = _make_options("NIFTY", "2026-07-31", [24500])
    with pytest.raises(ValueError):
        build_option_chain(options, get_index_config("NIFTY"), spot=0.0, expiry="2026-07-31")
