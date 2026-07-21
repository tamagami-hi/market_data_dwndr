"""Live index option-chain table (L1).

Holds the CE/PE integer arrays for one index's ATM ± 50 chain plus the spot/VIX
scalars. Ticks are applied in place (O(1) token -> strike index); the 1 Hz timer takes
a copy-on-snapshot ``IndexFrame`` so the writer thread never races the apply path.
docs/30-live-capture/live-data-pipeline.md.
"""

from __future__ import annotations

import numpy as np

from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.chain.assembler import ROLE_OPTION, ROLE_SPOT, ROLE_VIX, OptionChain
from app.kite import ticks as tickmod


def _copy_raw_block(block: RawBlock) -> RawBlock:
    return RawBlock({name: arr.copy() for name, arr in block.columns.items()})


class IndexTable:
    """Mutable L1 table for one index; snapshots to ``IndexFrame``."""

    def __init__(self, chain: OptionChain, risk_free_rate: float, trading_date: str) -> None:
        self.chain = chain
        self.risk_free_rate = risk_free_rate
        self.trading_date = trading_date
        n = chain.n_strikes
        self.calls = RawBlock.zeros(n)
        self.puts = RawBlock.zeros(n)
        self.spot_price = 0
        self.vix = 0
        self.sequence = 0
        self.unmatched = 0
        self.applied = 0

    @property
    def tokens(self) -> list[int]:
        return list(self.chain.token_map.keys())

    def apply_tick(self, tick: dict) -> bool:
        token = tick.get("instrument_token")
        role = self.chain.token_map.get(token)
        if role is None:
            self.unmatched += 1
            return False
        if role.kind == ROLE_SPOT:
            self.spot_price = tickmod.to_paise(tick.get("last_price"))
        elif role.kind == ROLE_VIX:
            self.vix = tickmod.to_paise(tick.get("last_price"))
        elif role.kind == ROLE_OPTION:
            block = self.calls if role.side == "CE" else self.puts
            self._write_block(block, int(role.index), tick)
        self.applied += 1
        return True

    def _write_block(self, block: RawBlock, idx: int, tick: dict) -> None:
        c = block.columns
        c["ltp"][idx] = tickmod.to_paise(tick.get("last_price"))
        c["oi"][idx] = tickmod.field_int(tick, "oi")
        c["volume"][idx] = tickmod.field_int(tick, "volume_traded")
        c["buy_quantity"][idx] = tickmod.field_int(tick, "total_buy_quantity")
        c["sell_quantity"][idx] = tickmod.field_int(tick, "total_sell_quantity")
        bid_p, bid_q, ask_p, ask_q = tickmod.best_bid_ask(tick)
        c["bid"][idx] = bid_p
        c["bid_qty"][idx] = bid_q
        c["ask"][idx] = ask_p
        c["ask_qty"][idx] = ask_q
        c["oi_day_high"][idx] = tickmod.field_int(tick, "oi_day_high")
        c["oi_day_low"][idx] = tickmod.field_int(tick, "oi_day_low")
        o, h, low, close = tickmod.ohlc_paise(tick)
        c["ohlc_open"][idx] = o
        c["ohlc_high"][idx] = h
        c["ohlc_low"][idx] = low
        c["ohlc_close"][idx] = close

    def snapshot(self, timestamp_unix_ms: int) -> IndexFrame:
        """Copy current state into an ``IndexFrame`` and advance the sequence."""
        frame = IndexFrame(
            timestamp_unix_ms=timestamp_unix_ms,
            sequence=self.sequence,
            spot_price=int(self.spot_price),
            vix=int(self.vix),
            calls=_copy_raw_block(self.calls),
            puts=_copy_raw_block(self.puts),
        )
        self.sequence += 1
        return frame

    def header(self) -> IndexHeader:
        return IndexHeader(
            trading_date=self.trading_date,
            underlying=self.chain.underlying,
            expiry_date=self.chain.expiry,
            risk_free_rate=self.risk_free_rate,
            strikes=np.array(self.chain.strikes, dtype="<i8"),
        )
