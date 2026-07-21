"""Tests for CalSpread F&O board discovery."""

from __future__ import annotations

from app.kite.instruments import Instrument
from app.stocks.board import (
    board_to_stock_refs,
    build_board,
    build_board_token_map,
    discover_fno_board,
    parse_universe,
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



# --- STOCK_UNIVERSE parsing + discovery bootstrap ----------------------------


def test_parse_universe():
    assert parse_universe("all") is None
    assert parse_universe("") is None
    assert parse_universe(None) is None
    assert parse_universe("RELIANCE, m&m") == {"RELIANCE", "M&M"}


class _FakeStore:
    """Stand-in for InstrumentStore.get(exchange, date)."""

    def __init__(self, nfo, nse):
        self._by_exchange = {"NFO": nfo, "NSE": nse}
        self.calls = []

    def get(self, exchange, trading_date, refresh=False):
        self.calls.append((exchange, trading_date, refresh))
        return self._by_exchange[exchange]


def test_discover_fno_board_is_fno_only():
    nfo, nse = _sample_instruments()
    store = _FakeStore(nfo, nse)
    board = discover_fno_board(store, "2026-07-21")
    # NIFTY index future has no NSE EQ row -> excluded; only real F&O stocks remain.
    assert [e.name for e in board] == ["M&M", "RELIANCE"]
    assert ("NFO", "2026-07-21", False) in store.calls
    assert ("NSE", "2026-07-21", False) in store.calls


def test_discover_fno_board_respects_stock_universe():
    nfo, nse = _sample_instruments()
    board = discover_fno_board(_FakeStore(nfo, nse), "2026-07-21", stock_universe="RELIANCE")
    assert [e.name for e in board] == ["RELIANCE"]
