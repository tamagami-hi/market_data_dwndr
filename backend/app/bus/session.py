"""Build the bus session/metadata payload from live TickVault capture state.

A consumer receives ticks keyed only by ``instrument_token``. Without a map it cannot tell
a NIFTY 24000 CE from a RELIANCE future, and it must not need access to TickVault's private
instrument files or state directory to find out. This module derives that map from the
objects capture already holds — the option chains, the stock board, and the index-F&O board
— so the published metadata always describes exactly the universe being ticked.

Nothing here reads credentials. The payload carries instrument identity only; access
tokens and passcodes never enter bus messages.
"""

from __future__ import annotations

from app.bus.protocol import InstrumentInfo
from app.chain.config import VIX_SYMBOL, VIX_TOKEN, get_index_config

DOMAIN_INDEX_OPTION = "index_option"
DOMAIN_INDEX_SPOT = "index_spot"
DOMAIN_INDEX_FUTURE = "index_future"
DOMAIN_STOCK_SPOT = "stock_spot"
DOMAIN_STOCK_FUTURE = "stock_future"
DOMAIN_VOLATILITY = "volatility"


def _index_exchange(underlying: str) -> str:
    try:
        return get_index_config(underlying).options_exchange
    except Exception:  # noqa: BLE001 - unknown index must not break metadata
        return ""


def _spot_exchange(underlying: str) -> str:
    """Exchange for an index's spot, taken from the ``EXCHANGE:SYMBOL`` spot symbol."""
    try:
        symbol = get_index_config(underlying).spot_symbol
    except Exception:  # noqa: BLE001
        return ""
    return symbol.split(":", 1)[0] if ":" in symbol else ""


def index_instruments(index_tables: dict) -> list[InstrumentInfo]:
    """Option-chain legs plus each index's spot, from the live ``OptionChain`` objects."""
    out: list[InstrumentInfo] = []
    seen: set[int] = set()
    for name, table in index_tables.items():
        chain = table.chain
        exchange = _index_exchange(name)
        for side, tokens in (("CE", chain.call_tokens), ("PE", chain.put_tokens)):
            for position, token in enumerate(tokens):
                token = int(token)
                if token == 0 or token in seen:
                    continue
                seen.add(token)
                strike_paise = int(chain.strikes[position])
                out.append(
                    InstrumentInfo(
                        instrument_token=token,
                        exchange=exchange,
                        tradingsymbol="",
                        instrument_type=side,
                        domain=DOMAIN_INDEX_OPTION,
                        underlying=chain.underlying,
                        expiry=chain.expiry,
                        strike_paise=strike_paise,
                        option_side=side,
                    )
                )
        try:
            spot_token = int(get_index_config(name).spot_token)
        except Exception:  # noqa: BLE001
            spot_token = 0
        if spot_token and spot_token not in seen:
            seen.add(spot_token)
            out.append(
                InstrumentInfo(
                    instrument_token=spot_token,
                    exchange=_spot_exchange(name),
                    tradingsymbol=name,
                    instrument_type="INDEX",
                    domain=DOMAIN_INDEX_SPOT,
                    underlying=name,
                )
            )
    return out


def stock_instruments(stock_matrix) -> list[InstrumentInfo]:
    """Spot and futures legs for every row of the F&O stock board."""
    out: list[InstrumentInfo] = []
    if stock_matrix is None:
        return out
    for ref in stock_matrix.stock_refs:
        out.append(
            InstrumentInfo(
                instrument_token=int(ref.spot_token),
                exchange="NSE",
                tradingsymbol=ref.tradingsymbol,
                instrument_type="EQ",
                domain=DOMAIN_STOCK_SPOT,
                underlying=ref.name,
            )
        )
        for future in ref.futures:
            out.append(
                InstrumentInfo(
                    instrument_token=int(future.token),
                    exchange="NFO",
                    tradingsymbol=ref.tradingsymbol,
                    instrument_type="FUT",
                    domain=DOMAIN_STOCK_FUTURE,
                    underlying=ref.name,
                    expiry=future.expiry,
                )
            )
    return out


def index_fno_instruments(index_fno_matrix) -> list[InstrumentInfo]:
    """Index spot + index futures from the consolidated index-F&O board."""
    out: list[InstrumentInfo] = []
    if index_fno_matrix is None:
        return out
    for ref in index_fno_matrix.index_refs:
        out.append(
            InstrumentInfo(
                instrument_token=int(ref.spot_token),
                exchange=_spot_exchange(ref.underlying),
                tradingsymbol=ref.spot_symbol,
                instrument_type="INDEX",
                domain=DOMAIN_INDEX_SPOT,
                underlying=ref.underlying,
            )
        )
        for future in ref.futures:
            out.append(
                InstrumentInfo(
                    instrument_token=int(future.token),
                    exchange=_index_exchange(ref.underlying) or "NFO",
                    tradingsymbol=ref.spot_symbol,
                    instrument_type="FUT",
                    domain=DOMAIN_INDEX_FUTURE,
                    underlying=ref.underlying,
                    expiry=future.expiry,
                )
            )
    return out


def build_instruments(
    index_tables: dict | None = None,
    stock_matrix=None,
    index_fno_matrix=None,
) -> list[InstrumentInfo]:
    """The full published universe, de-duplicated by token, in a stable order.

    Tokens can legitimately appear in more than one capture domain (an index spot is both
    an option-chain input and an index-F&O row), so the first description wins and the
    result stays one entry per token.
    """
    collected: list[InstrumentInfo] = []
    collected.extend(index_instruments(index_tables or {}))
    collected.extend(index_fno_instruments(index_fno_matrix))
    collected.extend(stock_instruments(stock_matrix))
    collected.append(
        InstrumentInfo(
            instrument_token=int(VIX_TOKEN),
            exchange="NSE",
            tradingsymbol=VIX_SYMBOL.split(":", 1)[-1],
            instrument_type="INDEX",
            domain=DOMAIN_VOLATILITY,
            underlying="INDIA VIX",
        )
    )
    unique: dict[int, InstrumentInfo] = {}
    for info in collected:
        if info.instrument_token and info.instrument_token not in unique:
            unique[info.instrument_token] = info
    return list(unique.values())
