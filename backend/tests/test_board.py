"""Tests for CalSpread F&O board discovery."""

from __future__ import annotations

from app.kite.instruments import Instrument
from app.stocks.board import (
    board_to_stock_refs,
    build_board,
    build_board_token_map,
)


def _eq(symbol: str, token: int, lot: int) -> Instrument:
    return Instrument(
        instrument_token=token, exchange_token=token, tradingsymbol=symbol, name=symbol,
        last_price=0.0, expiry="", strike=0.0, tick_size=0.05, lot_size=lot,
        instrument_type="EQ", segment="NSE", exchange="NSE",
    )


def _fut(name: str, token: int, expiry: str, lot: int) -> Instrument:
    return Instrument(
        instrument_token=token, exchange_token=token, tradingsymbol=f"{name}{expiry}FUT",
        name=name, last_price=0.0, expiry=expiry, strike=0.0, tick_size=0.05, lot_size=lot,
        instrument_type="FUT", segment="NFO-FUT", exchange="NFO",
    )


def _sample_instruments():
    nse = [
        _eq("RELIANCE", 738561, 250),
        _eq("M&M", 519937, 700),
        # No EQ row for NIFTY -> it is an index and must be excluded.
    ]
    nfo = [
        # RELIANCE: 4 futures -> keep nearest 3.
        _fut("RELIANCE", 1004, "2026-10-30", 250),
        _fut("RELIANCE", 1001, "2026-07-31", 250),
        _fut("RELIANCE", 1003, "2026-09-25", 250),
        _fut("RELIANCE", 1002, "2026-08-28", 250),
        # M&M: only 2 futures.
        _fut("M&M", 2002, "2026-08-28", 700),
        _fut("M&M", 2001, "2026-07-31", 700),
        # NIFTY index futures (no EQ) -> excluded.
        _fut("NIFTY", 3001, "2026-07-31", 50),
    ]
    return nfo, nse


def test_build_board_matches_spot_and_keeps_three_nearest_futures():
    nfo, nse = _sample_instruments()
    board = build_board(nfo, nse)

    names = [e.name for e in board]
    assert names == ["M&M", "RELIANCE"]  # sorted, NIFTY excluded

    reliance = next(e for e in board if e.name == "RELIANCE")
    # 4 futures present -> nearest 3 by expiry, ascending.
    assert [f.expiry for f in reliance.futures] == ["2026-07-31", "2026-08-28", "2026-09-25"]
    assert [f.instrument_token for f in reliance.futures] == [1001, 1002, 1003]

    mm = next(e for e in board if e.name == "M&M")
    assert len(mm.futures) == 2  # fewer than 3


def test_allow_list_restricts_universe():
    nfo, nse = _sample_instruments()
    board = build_board(nfo, nse, allow={"RELIANCE"})
    assert [e.name for e in board] == ["RELIANCE"]


def test_board_to_stock_refs():
    nfo, nse = _sample_instruments()
    refs = board_to_stock_refs(build_board(nfo, nse))
    reliance = next(r for r in refs if r.name == "RELIANCE")
    assert reliance.tradingsymbol == "RELIANCE"
    assert reliance.spot_token == 738561
    assert reliance.lot_size == 250
    assert len(reliance.futures) == 3
    assert reliance.futures[0].expiry == "2026-07-31"


def test_build_board_token_map_routing():
    nfo, nse = _sample_instruments()
    board = build_board(nfo, nse)
    token_map = build_board_token_map(board)

    # Row order matches board order: M&M=row 0, RELIANCE=row 1.
    assert token_map[519937].row == 0 and token_map[519937].leg == "spot"
    assert token_map[738561].row == 1 and token_map[738561].leg == "spot"
    # RELIANCE nearest future -> fut_current on row 1.
    assert token_map[1001].row == 1 and token_map[1001].leg == "fut_current"
    assert token_map[1003].leg == "fut_far"
    # NIFTY future token was excluded from the board entirely.
    assert 3001 not in token_map
