"""Tests for the consolidated index-F&O capture domain."""

from __future__ import annotations

from app.bin_codec.reader import IndexFnoBinReader, StockBinReader
from app.bin_codec.writer import IndexFnoBinWriter
from app.index_fno.board import (
    build_index_fno_board,
    build_index_fno_token_map,
)
from app.index_fno.matrix import IndexFnoMatrix
from app.kite.instruments import Instrument


def _future(name: str, expiry: str, token: int, exchange: str = "NFO") -> Instrument:
    return Instrument(
        instrument_token=token,
        exchange_token=token // 256,
        tradingsymbol=f"{name}{expiry.replace('-', '')}FUT",
        name=name,
        last_price=0.0,
        expiry=expiry,
        strike=0.0,
        tick_size=0.05,
        lot_size=50,
        instrument_type="FUT",
        segment=f"{exchange}-FUT",
        exchange=exchange,
    )


def _equity(name: str, token: int) -> Instrument:
    return Instrument(
        instrument_token=token,
        exchange_token=token // 256,
        tradingsymbol=name,
        name=name,
        last_price=0.0,
        expiry="",
        strike=0.0,
        tick_size=0.05,
        lot_size=1,
        instrument_type="EQ",
        segment="NSE",
        exchange="NSE",
    )


def _dumps():
    """NFO carries NSE index + stock futures; BFO carries the BSE index futures."""
    return {
        "NFO": [
            _future("NIFTY", "2026-08-27", 1001),
            _future("NIFTY", "2026-09-24", 1002),
            _future("NIFTY", "2026-10-29", 1003),
            _future("NIFTY", "2026-11-26", 1004),  # 4th expiry: must be dropped
            _future("BANKNIFTY", "2026-08-27", 2001),
            _future("BANKNIFTY", "2026-09-24", 2002),
            _future("RELIANCE", "2026-08-27", 9001),  # a stock future, not an index
            _equity("RELIANCE", 9000),
        ],
        "BFO": [
            _future("SENSEX", "2026-08-27", 3001, exchange="BFO"),
            _future("SENSEX", "2026-09-24", 3002, exchange="BFO"),
        ],
    }


# --- board discovery ----------------------------------------------------------


def test_index_futures_are_kept_where_the_stock_board_discards_them():
    """The stock board drops these for want of an equity leg; this domain keeps them."""
    board = build_index_fno_board(_dumps(), ["NIFTY", "BANKNIFTY", "SENSEX"])

    assert [entry.underlying for entry in board] == ["NIFTY", "BANKNIFTY", "SENSEX"]
    assert [len(entry.futures) for entry in board] == [3, 2, 2]


def test_only_the_three_nearest_expiries_are_kept_in_order():
    board = build_index_fno_board(_dumps(), ["NIFTY"])

    expiries = [f.expiry for f in board[0].futures]
    assert expiries == ["2026-08-27", "2026-09-24", "2026-10-29"]  # the 4th is dropped
    assert expiries == sorted(expiries)


def test_stock_futures_are_not_mistaken_for_index_futures():
    board = build_index_fno_board(_dumps(), ["NIFTY", "BANKNIFTY", "SENSEX"])

    assert all(entry.underlying != "RELIANCE" for entry in board)


def test_row_order_follows_the_configured_index_order_not_dict_iteration():
    """On-disk row identity must be stable and configuration-driven."""
    board = build_index_fno_board(_dumps(), ["SENSEX", "NIFTY"])

    assert [entry.underlying for entry in board] == ["SENSEX", "NIFTY"]


def test_an_index_with_no_futures_is_skipped_rather_than_zero_filled():
    """A permanently zero row would be indistinguishable from a dead feed."""
    board = build_index_fno_board({"NFO": [], "BFO": []}, ["NIFTY"])

    assert board == []


def test_an_unsupported_index_name_is_skipped_without_raising():
    """get_index_config raises KeyError for excluded indices; discovery must not die."""
    board = build_index_fno_board(_dumps(), ["NIFTY", "MIDCPNIFTY", "BANKEX"])

    assert [entry.underlying for entry in board] == ["NIFTY"]


def test_the_domain_scales_to_whatever_index_set_is_configured():
    """Coverage is driven by configuration, so a 6-index set needs no code change here."""
    board_four = build_index_fno_board(_dumps(), ["NIFTY", "BANKNIFTY", "SENSEX"])
    board_one = build_index_fno_board(_dumps(), ["NIFTY"])

    assert len(board_four) == 3
    assert len(board_one) == 1


# --- token routing ------------------------------------------------------------


def test_every_token_routes_to_exactly_one_row_and_leg():
    board = build_index_fno_board(_dumps(), ["NIFTY", "BANKNIFTY"])
    token_map = build_index_fno_token_map(board)

    # NIFTY row 0: spot + 3 futures; BANKNIFTY row 1: spot + 2 futures.
    assert len(token_map) == 4 + 3
    assert token_map[256265].row == 0 and token_map[256265].leg == "spot"
    assert token_map[1001].leg == "fut_current"
    assert token_map[1002].leg == "fut_mid"
    assert token_map[1003].leg == "fut_far"
    assert token_map[2001].row == 1 and token_map[2001].leg == "fut_current"


def test_ticks_land_in_the_right_row_and_leg():
    board = build_index_fno_board(_dumps(), ["NIFTY", "BANKNIFTY"])
    matrix = IndexFnoMatrix(board, 0.07, "2026-08-10")

    assert matrix.apply_tick({"instrument_token": 1001, "last_price": 24_600.5}) is True
    assert matrix.apply_tick({"instrument_token": 2001, "last_price": 55_000.0}) is True
    assert matrix.apply_tick({"instrument_token": 777, "last_price": 1.0}) is False

    assert matrix.legs["fut_current"].scalars["ltp"][0] == 2_460_050
    assert matrix.legs["fut_current"].scalars["ltp"][1] == 5_500_000
    assert matrix.applied == 2
    assert matrix.unmatched == 1


def test_spot_and_futures_share_one_frame_so_basis_needs_no_join():
    """§9: one timestamp must describe the whole derivative universe."""
    board = build_index_fno_board(_dumps(), ["NIFTY"])
    matrix = IndexFnoMatrix(board, 0.07, "2026-08-10")
    matrix.apply_tick({"instrument_token": 256265, "last_price": 24_500.0})  # spot
    matrix.apply_tick({"instrument_token": 1001, "last_price": 24_600.0})  # near future

    frame = matrix.snapshot(1_700_000_000_000)

    assert frame.spot.scalars["ltp"][0] == 2_450_000
    assert frame.fut_current.scalars["ltp"][0] == 2_460_000
    # The basis is derivable at read time; it is deliberately NOT stored.
    assert not hasattr(frame, "basis")


def test_snapshot_copies_so_later_ticks_cannot_mutate_a_taken_frame():
    board = build_index_fno_board(_dumps(), ["NIFTY"])
    matrix = IndexFnoMatrix(board, 0.07, "2026-08-10")
    matrix.apply_tick({"instrument_token": 1001, "last_price": 100.0})

    frame = matrix.snapshot(1_000)
    matrix.apply_tick({"instrument_token": 1001, "last_price": 200.0})

    assert frame.fut_current.scalars["ltp"][0] == 10_000  # unchanged by the later tick
    assert matrix.legs["fut_current"].scalars["ltp"][0] == 20_000


def test_sequence_advances_once_per_snapshot():
    board = build_index_fno_board(_dumps(), ["NIFTY"])
    matrix = IndexFnoMatrix(board, 0.07, "2026-08-10")

    assert matrix.snapshot(1_000).sequence == 0
    assert matrix.snapshot(2_000).sequence == 1


# --- codec round trip ---------------------------------------------------------


def test_header_and_frames_round_trip_through_the_binary_format(tmp_path):
    board = build_index_fno_board(_dumps(), ["NIFTY", "BANKNIFTY", "SENSEX"])
    matrix = IndexFnoMatrix(board, 0.0691, "2026-08-10")
    matrix.apply_tick({"instrument_token": 256265, "last_price": 24_500.0})
    matrix.apply_tick(
        {
            "instrument_token": 1001,
            "last_price": 24_600.0,
            "oi": 12_345,
            "volume_traded": 999,
            "depth": {
                "buy": [{"price": 24_599.0, "quantity": 50, "orders": 2}],
                "sell": [{"price": 24_601.0, "quantity": 75, "orders": 3}],
            },
        }
    )

    path = tmp_path / "INDICES_FnO" / "2026-08-10.bin"
    writer = IndexFnoBinWriter(path, sync=False)
    writer.open()
    assert writer.write_header(matrix.header()) is True
    writer.append_frame(matrix.snapshot(1_700_000_000_000))
    writer.append_frame(matrix.snapshot(1_700_000_001_000))
    writer.close()

    with IndexFnoBinReader(path) as reader:
        header = reader.header()
        assert header.trading_date == "2026-08-10"
        assert round(header.risk_free_rate, 4) == 0.0691
        assert [ref.underlying for ref in header.indices] == ["NIFTY", "BANKNIFTY", "SENSEX"]
        # The header records the token universe, so a reader can tell which instrument
        # backed each row without consulting anything else.
        assert header.indices[0].spot_token == 256265
        assert [f.token for f in header.indices[0].futures] == [1001, 1002, 1003]
        assert [f.expiry for f in header.indices[2].futures] == ["2026-08-27", "2026-09-24"]

        assert len(reader) == 2
        frame = reader.frame(0)
        assert frame.timestamp_unix_ms == 1_700_000_000_000
        assert frame.sequence == 0
        assert frame.spot.scalars["ltp"][0] == 2_450_000
        assert frame.fut_current.scalars["ltp"][0] == 2_460_000
        assert frame.fut_current.scalars["oi"][0] == 12_345
        assert frame.fut_current.depth[0]["bid_price"][0] == 2_459_900
        assert frame.fut_current.depth[0]["ask_qty"][0] == 75
        assert reader.frame(1).timestamp_unix_ms == 1_700_000_001_000


def test_the_file_appends_across_a_restart_without_a_second_header(tmp_path):
    """Restart safety: the header is written only when the file is empty."""
    board = build_index_fno_board(_dumps(), ["NIFTY"])
    matrix = IndexFnoMatrix(board, 0.07, "2026-08-10")
    path = tmp_path / "INDICES_FnO" / "2026-08-10.bin"

    first = IndexFnoBinWriter(path, sync=False)
    first.open()
    first.write_header(matrix.header())
    first.append_frame(matrix.snapshot(1_000))
    first.close()

    resumed = IndexFnoBinWriter(path, sync=False)
    resumed.open()
    assert resumed.write_header(matrix.header()) is False  # not written a second time
    resumed.append_frame(matrix.snapshot(2_000))
    resumed.close()

    with IndexFnoBinReader(path) as reader:
        assert len(reader) == 2
        assert [f.timestamp_unix_ms for f in reader.frames()] == [1_000, 2_000]


def test_the_new_dataset_is_scannable_with_no_format_specific_changes(tmp_path):
    """scan_frames keys only off the shared framing, so completeness works for free."""
    from app.bin_codec.scan import scan_frames

    board = build_index_fno_board(_dumps(), ["NIFTY"])
    matrix = IndexFnoMatrix(board, 0.07, "2026-08-10")
    path = tmp_path / "INDICES_FnO" / "2026-08-10.bin"
    writer = IndexFnoBinWriter(path, sync=False)
    writer.open()
    writer.write_header(matrix.header())
    for second in range(5):
        writer.append_frame(matrix.snapshot(1_000 + second * 1_000))
    writer.close()

    scan = scan_frames(path, collect_timestamps=True)

    assert scan.frames == 5
    assert scan.first_timestamp_ms == 1_000
    assert scan.last_timestamp_ms == 5_000
    assert scan.timestamps == (1_000, 2_000, 3_000, 4_000, 5_000)


def test_the_stock_dataset_is_untouched_by_the_new_domain(tmp_path):
    """§2/§26: the stock F&O binary contract must remain exactly as it was."""
    from app.bin_codec.writer import StockBinWriter
    from app.stocks.board import build_board
    from app.stocks.matrix import StockMatrix
    from tests.test_board import _sample_instruments

    nfo, nse = _sample_instruments()
    stock_matrix = StockMatrix(build_board(nfo, nse), 0.07, "2026-08-10")
    path = tmp_path / "STOCKS" / "2026-08-10.bin"
    writer = StockBinWriter(path, sync=False)
    writer.open()
    writer.write_header(stock_matrix.header())
    writer.append_frame(stock_matrix.snapshot(1_000))
    writer.close()

    with StockBinReader(path) as reader:
        header = reader.header()
        assert header.schema_version == 1  # unchanged
        assert reader.frame(0).timestamp_unix_ms == 1_000
        assert len(header.stocks) > 0
