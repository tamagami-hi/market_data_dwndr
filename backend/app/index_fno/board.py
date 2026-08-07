"""Index-F&O board discovery: index futures for the configured indices.

Index futures were previously invisible to TickVault. They are present in the instrument
dumps already being fetched, and ``stocks.board.build_board`` even collects them — then
discards them, because that function requires a matching NSE ``EQ`` row and an index has
none (``board.py``: "no equity leg -> index/non-stock, excluded"). That exclusion is
correct for the *stock* board; this module is the counterpart that keeps them.

Scope is driven entirely by ``INDEX_CONFIGS`` / ``settings.indices``, so the domain covers
whatever index set is configured. Adding an index is a config entry, not a code change
here — which matters because the supported set is a product decision recorded in
docs/90-decisions, not something this module should encode independently.

Futures for NSE indices live on NFO and for BSE indices on BFO, so both dumps are
consulted; ``IndexConfig.options_exchange`` already records which exchange an index trades
its derivatives on.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.bin_codec.layout import FutureRef, IndexFnoRef
from app.chain.config import IndexConfig, get_index_config
from app.kite.instruments import Instrument, InstrumentStore

# Kept in step with the stock board so both consolidated datasets share one leg shape.
MAX_FUTURES = 3
LEG_SLOTS = ("spot", "fut_current", "fut_mid", "fut_far")

# Exchanges that carry index derivatives.
DERIV_EXCHANGES = ("NFO", "BFO")


@dataclass
class IndexFnoEntry:
    """One index's spot reference plus up to 3 nearest futures (ascending expiry)."""

    underlying: str
    spot_symbol: str
    spot_token: int
    futures: list[Instrument] = field(default_factory=list)


@dataclass(frozen=True)
class IndexFnoRole:
    """Routing target for a subscribed token: matrix row + which leg."""

    row: int
    leg: str  # one of LEG_SLOTS


def build_index_fno_board(
    instruments_by_exchange: dict[str, list[Instrument]],
    index_names: list[str] | tuple[str, ...],
) -> list[IndexFnoEntry]:
    """Derive the index-F&O board for ``index_names``.

    Rows come out in the order given, so the on-disk row order is the configured index
    order rather than an accident of dict iteration. An index with no futures in the dump
    is skipped: a row of permanently zero-filled futures would be indistinguishable from a
    dead feed for that index.
    """
    futures_by_name: dict[str, list[Instrument]] = defaultdict(list)
    for instruments in instruments_by_exchange.values():
        for inst in instruments:
            if inst.instrument_type == "FUT" and inst.name and inst.expiry:
                futures_by_name[inst.name].append(inst)

    entries: list[IndexFnoEntry] = []
    for name in index_names:
        try:
            config: IndexConfig = get_index_config(name)
        except KeyError:
            continue  # not a supported index; the caller reports skips
        futures = sorted(futures_by_name.get(config.underlying, []), key=lambda f: f.expiry)
        if not futures:
            continue
        entries.append(
            IndexFnoEntry(
                underlying=config.underlying,
                spot_symbol=config.spot_symbol,
                spot_token=config.spot_token,
                futures=futures[:MAX_FUTURES],
            )
        )
    return entries


def discover_index_fno_board(
    instrument_store: InstrumentStore,
    trading_date: str,
    index_names: list[str] | tuple[str, ...],
    *,
    refresh: bool = False,
) -> list[IndexFnoEntry]:
    """Fetch the derivative dumps and derive the index-F&O board."""
    dumps: dict[str, list[Instrument]] = {}
    for exchange in DERIV_EXCHANGES:
        try:
            dumps[exchange] = instrument_store.get(exchange, trading_date, refresh=refresh)
        except Exception:  # noqa: BLE001 - a missing dump must not kill the session
            dumps[exchange] = []
    return build_index_fno_board(dumps, index_names)


def board_to_index_refs(board: list[IndexFnoEntry]) -> list[IndexFnoRef]:
    """Convert the board into ``IndexFnoHeader`` rows (fixed order)."""
    return [
        IndexFnoRef(
            underlying=entry.underlying,
            spot_symbol=entry.spot_symbol,
            spot_token=entry.spot_token,
            futures=[
                FutureRef(token=f.instrument_token, expiry=f.expiry, lot_size=f.lot_size)
                for f in entry.futures
            ],
        )
        for entry in board
    ]


def build_index_fno_token_map(board: list[IndexFnoEntry]) -> dict[int, IndexFnoRole]:
    """Map every subscribed token to its matrix row + leg slot for O(1) routing."""
    token_map: dict[int, IndexFnoRole] = {}
    for row, entry in enumerate(board):
        token_map[entry.spot_token] = IndexFnoRole(row=row, leg="spot")
        for slot, fut in enumerate(entry.futures):
            token_map[fut.instrument_token] = IndexFnoRole(row=row, leg=LEG_SLOTS[slot + 1])
    return token_map


__all__ = [
    "DERIV_EXCHANGES",
    "LEG_SLOTS",
    "MAX_FUTURES",
    "IndexFnoEntry",
    "IndexFnoRole",
    "board_to_index_refs",
    "build_index_fno_board",
    "build_index_fno_token_map",
    "discover_index_fno_board",
]
