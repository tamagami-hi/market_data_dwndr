"""Tests for the live index table (L1) and stock matrix (L5): apply -> snapshot -> .bin."""

from __future__ import annotations

from app.bin_codec import writer
from app.bin_codec.reader import IndexBinReader, StockBinReader
from app.chain.assembler import build_option_chain
from app.chain.config import VIX_TOKEN, get_index_config
from app.chain.table import IndexTable
from app.stocks.matrix import StockMatrix
from tests.test_board import _sample_instruments  # reuse board fixtures
from tests.test_chain import _make_options  # reuse option builder


def _full_tick(token: int, ltp: float, *, oi=0, buy=None, sell=None, ohlc=None) -> dict:
    tick = {
        "instrument_token": token,
        "last_price": ltp,
        "oi": oi,
        "volume_traded": 100,
        "total_buy_quantity": 10,
        "total_sell_quantity": 20,
        "oi_day_high": oi + 5,
        "oi_day_low": max(oi - 5, 0),
        "ohlc": ohlc or {"open": ltp, "high": ltp, "low": ltp, "close": ltp},
    }
    if buy is not None or sell is not None:
        tick["depth"] = {"buy": buy or [], "sell": sell or []}
    return tick


# --- IndexTable (L1) ---------------------------------------------------------


def test_index_table_apply_snapshot_roundtrip(tmp_path):
    strikes = list(range(24000, 25001, 50))
    options = _make_options("NIFTY", "2026-07-31", strikes)
    chain = build_option_chain(
        options, get_index_config("NIFTY"), spot=24567.0, expiry="2026-07-31"
    )
    table = IndexTable(chain, risk_free_rate=0.0691, trading_date="2026-07-21")

    ce_token = int(chain.call_tokens[0])
    pe_token = int(chain.put_tokens[3])

    table.apply_tick(
        _full_tick(
            ce_token,
            123.45,
            oi=5000,
            buy=[{"price": 123.40, "quantity": 50, "orders": 3}],
            sell=[{"price": 123.55, "quantity": 40, "orders": 2}],
        )
    )
    table.apply_tick(_full_tick(pe_token, 88.20, oi=7000))
    table.apply_tick(_full_tick(get_index_config("NIFTY").spot_token, 24567.0))
    table.apply_tick(_full_tick(VIX_TOKEN, 12.34))
    assert table.apply_tick(_full_tick(999999999, 1.0)) is False  # unknown
    assert table.unmatched == 1

    path = tmp_path / "NIFTY" / "2026-07-21.bin"
    with writer.IndexBinWriter(path) as w:
        w.write_header(table.header())
        w.append_frame(table.snapshot(1_753_070_400_000))

    with IndexBinReader(path) as r:
        assert r.header().underlying == "NIFTY"
        frame = r.frame(0)
        assert frame.spot_price == 2456700  # 24567.00 -> paise
        assert frame.vix == 1234
        assert frame.calls.columns["ltp"][0] == 12345
        assert frame.calls.columns["oi"][0] == 5000
        assert frame.calls.columns["bid"][0] == 12340
        assert frame.calls.columns["ask"][0] == 12355
        assert frame.calls.columns["bid_qty"][0] == 50
        assert frame.puts.columns["ltp"][3] == 8820
        assert frame.puts.columns["oi"][3] == 7000
        # L1: RawBlock has no multi-level depth arrays -- just best bid/ask columns.
        assert "bid" in frame.calls.columns and "ask" in frame.calls.columns


def test_index_table_sequence_increments():
    options = _make_options("NIFTY", "2026-07-31", [24500, 24550, 24600])
    chain = build_option_chain(
        options, get_index_config("NIFTY"), spot=24550.0, expiry="2026-07-31"
    )
    table = IndexTable(chain, 0.07, "2026-07-21")
    assert table.snapshot(1000).sequence == 0
    assert table.snapshot(2000).sequence == 1
    assert table.snapshot(3000).sequence == 2


# --- StockMatrix (L5) --------------------------------------------------------


def _depth5(base_price: float):
    return [
        {"price": base_price - i * 0.05, "quantity": 100 + i, "orders": i + 1}
        for i in range(5)
    ]


def test_stock_matrix_apply_snapshot_roundtrip(tmp_path):
    nfo, nse = _sample_instruments()
    from app.stocks.board import build_board

    board = build_board(nfo, nse)  # row 0 = M&M, row 1 = RELIANCE
    matrix = StockMatrix(board, risk_free_rate=0.0691, trading_date="2026-07-21")

    # M&M spot (token 519937, row 0), full L5 depth on the buy side.
    matrix.apply_tick(
        _full_tick(519937, 2950.50, oi=1000, buy=_depth5(2950.45), sell=_depth5(2950.55))
    )
    # RELIANCE nearest future (token 1001) -> fut_current, row 1.
    matrix.apply_tick(_full_tick(1001, 2460.00, oi=8000, buy=_depth5(2459.90)))
    assert matrix.apply_tick(_full_tick(3001, 1.0)) is False  # excluded NIFTY fut token
    assert matrix.unmatched == 1

    path = tmp_path / "STOCKS" / "2026-07-21.bin"
    with writer.StockBinWriter(path) as w:
        w.write_header(matrix.header())
        w.append_frame(matrix.snapshot(1_753_070_400_000))

    with StockBinReader(path) as r:
        header = r.header()
        assert [s.name for s in header.stocks] == ["M&M", "RELIANCE"]
        frame = r.frame(0)
        # spot leg, M&M row 0
        assert frame.spot.scalars["ltp"][0] == 295050
        assert frame.spot.scalars["oi"][0] == 1000
        # L5: 5 depth levels present
        assert len(frame.spot.depth) == 5
        assert frame.spot.depth[0]["bid_price"][0] == 295045
        assert frame.spot.depth[4]["bid_qty"][0] == 104
        assert frame.spot.depth[0]["ask_price"][0] == 295055
        # future leg, RELIANCE row 1
        assert frame.fut_current.scalars["ltp"][1] == 246000
        assert frame.fut_current.depth[0]["bid_price"][1] == 245990
        # untouched cells remain zero
        assert frame.fut_mid.scalars["ltp"][0] == 0
