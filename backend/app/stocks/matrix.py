"""Live stock matrix (L5).

Four instrument legs (spot + 3 futures) x N stocks, each a column matrix with 5-level
depth. Ticks route by token to ``(row, leg)`` (O(1)); the 1 Hz timer snapshots to a
copy-on-write ``StockFrame``. docs/30-live-capture/stocks-capture.md.
"""

from __future__ import annotations

from app.bin_codec.layout import DEPTH_LEVELS, InstrColumns, StockFrame, StockHeader
from app.kite import ticks as tickmod
from app.stocks.board import (
    LEG_SLOTS,
    BoardEntry,
    board_to_stock_refs,
    build_board_token_map,
)


def _copy_instr(instr: InstrColumns) -> InstrColumns:
    return InstrColumns(
        scalars={name: arr.copy() for name, arr in instr.scalars.items()},
        depth=[{name: arr.copy() for name, arr in level.items()} for level in instr.depth],
    )


class StockMatrix:
    """Mutable L5 matrix for all F&O stocks; snapshots to ``StockFrame``."""

    def __init__(
        self, board: list[BoardEntry], risk_free_rate: float, trading_date: str
    ) -> None:
        self.board = board
        self.risk_free_rate = risk_free_rate
        self.trading_date = trading_date
        self.stock_refs = board_to_stock_refs(board)
        self.token_map = build_board_token_map(board)
        n = len(board)
        # LEG_SLOTS = ("spot", "fut_current", "fut_mid", "fut_far")
        self.legs: dict[str, InstrColumns] = {leg: InstrColumns.zeros(n) for leg in LEG_SLOTS}
        self.sequence = 0
        self.unmatched = 0
        self.applied = 0

    @property
    def tokens(self) -> list[int]:
        return list(self.token_map.keys())

    def apply_tick(self, tick: dict) -> bool:
        token = tick.get("instrument_token")
        role = self.token_map.get(token)
        if role is None:
            self.unmatched += 1
            return False
        self._write_row(self.legs[role.leg], role.row, tick)
        self.applied += 1
        return True

    def _write_row(self, instr: InstrColumns, row: int, tick: dict) -> None:
        s = instr.scalars
        s["ltp"][row] = tickmod.to_paise(tick.get("last_price"))
        s["oi"][row] = tickmod.field_int(tick, "oi")
        s["volume"][row] = tickmod.field_int(tick, "volume_traded")
        s["buy_quantity"][row] = tickmod.field_int(tick, "total_buy_quantity")
        s["sell_quantity"][row] = tickmod.field_int(tick, "total_sell_quantity")
        s["oi_day_high"][row] = tickmod.field_int(tick, "oi_day_high")
        s["oi_day_low"][row] = tickmod.field_int(tick, "oi_day_low")
        o, h, low, close = tickmod.ohlc_paise(tick)
        s["ohlc_open"][row] = o
        s["ohlc_high"][row] = h
        s["ohlc_low"][row] = low
        s["ohlc_close"][row] = close

        buy = tickmod.depth_side(tick, "buy", DEPTH_LEVELS)
        sell = tickmod.depth_side(tick, "sell", DEPTH_LEVELS)
        for level in range(DEPTH_LEVELS):
            d = instr.depth[level]
            bid_p, bid_q, bid_o = buy[level]
            ask_p, ask_q, ask_o = sell[level]
            d["bid_price"][row] = bid_p
            d["bid_qty"][row] = bid_q
            d["bid_orders"][row] = bid_o
            d["ask_price"][row] = ask_p
            d["ask_qty"][row] = ask_q
            d["ask_orders"][row] = ask_o

    def snapshot(self, timestamp_unix_ms: int) -> StockFrame:
        frame = StockFrame(
            timestamp_unix_ms=timestamp_unix_ms,
            sequence=self.sequence,
            spot=_copy_instr(self.legs["spot"]),
            fut_current=_copy_instr(self.legs["fut_current"]),
            fut_mid=_copy_instr(self.legs["fut_mid"]),
            fut_far=_copy_instr(self.legs["fut_far"]),
        )
        self.sequence += 1
        return frame

    def header(self) -> StockHeader:
        return StockHeader(
            trading_date=self.trading_date,
            risk_free_rate=self.risk_free_rate,
            stocks=self.stock_refs,
        )
