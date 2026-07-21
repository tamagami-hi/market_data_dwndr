"""CalSpread F&O board discovery.

Ported from CalSpread ``deriveFnoBoard`` / ``deriveFnoStocks``
(docs/30-live-capture/stocks-capture.md):

1. Every NFO ``FUT`` row with a ``name`` -> that name is an underlying; collect its
   futures.
2. Match to an NSE ``EQ`` row by ``tradingsymbol`` -> the spot (+ lot_size).
   Underlyings with no EQ row are indices -> excluded here.
3. Sort each underlying's futures by expiry, keep the 3 nearest: [current, mid, far].

Re-derived daily so expiry roll-over is automatic. The resolved board becomes the
``StockHeader`` (rows in a fixed order) and a token->(row, leg) routing map.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.bin_codec.layout import FutureRef, StockRef
from app.kite.instruments import Instrument

MAX_FUTURES = 3
LEG_SLOTS = ("spot", "fut_current", "fut_mid", "fut_far")


@dataclass
class BoardEntry:
    """One underlying's spot + up to 3 nearest futures (by expiry)."""

    name: str
    spot: Instrument
    futures: list[Instrument]  # 1..3, ascending expiry [current, mid, far]


@dataclass(frozen=True)
class StockRole:
    """Routing target for a subscribed token: matrix row + which leg."""

    row: int
    leg: str  # one of LEG_SLOTS


def build_board(
    nfo_instruments: list[Instrument],
    nse_instruments: list[Instrument],
    allow: set[str] | None = None,
) -> list[BoardEntry]:
    """Derive the F&O board. ``allow`` optionally restricts to specific underlyings."""
    futures_by_name: dict[str, list[Instrument]] = defaultdict(list)
    for inst in nfo_instruments:
        if inst.instrument_type == "FUT" and inst.name and inst.expiry:
            futures_by_name[inst.name].append(inst)

    eq_by_symbol = {
        inst.tradingsymbol: inst
        for inst in nse_instruments
        if inst.instrument_type == "EQ"
    }

    entries: list[BoardEntry] = []
    for name in sorted(futures_by_name):
        if allow is not None and name not in allow:
            continue
        spot = eq_by_symbol.get(name)
        if spot is None:
            continue  # no equity leg -> index/non-stock, excluded
        futures = sorted(futures_by_name[name], key=lambda f: f.expiry)[:MAX_FUTURES]
        entries.append(BoardEntry(name=name, spot=spot, futures=futures))
    return entries


def board_to_stock_refs(board: list[BoardEntry]) -> list[StockRef]:
    """Convert the board into ``StockHeader`` rows (fixed order)."""
    refs: list[StockRef] = []
    for entry in board:
        refs.append(
            StockRef(
                tradingsymbol=entry.spot.tradingsymbol,
                name=entry.name,
                spot_token=entry.spot.instrument_token,
                lot_size=entry.spot.lot_size,
                futures=[
                    FutureRef(token=f.instrument_token, expiry=f.expiry, lot_size=f.lot_size)
                    for f in entry.futures
                ],
            )
        )
    return refs


def build_board_token_map(board: list[BoardEntry]) -> dict[int, StockRole]:
    """Map every subscribed token to its matrix row + leg slot for O(1) routing."""
    token_map: dict[int, StockRole] = {}
    for row, entry in enumerate(board):
        token_map[entry.spot.instrument_token] = StockRole(row=row, leg="spot")
        for slot, fut in enumerate(entry.futures):
            token_map[fut.instrument_token] = StockRole(row=row, leg=LEG_SLOTS[slot + 1])
    return token_map
