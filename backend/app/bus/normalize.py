"""Turn a raw KiteTicker tick dict into a :class:`NormTick`.

Every field goes through the **same** helpers the capture path uses
(``app.kite.ticks``), so the bus cannot publish a different interpretation of a value
than the one TickVault applied to its tables and wrote to BIN. That is the whole point:
a consumer reading the bus and a consumer reading the archive must agree.

The two exchange timestamps are the only fields the capture path does not store. They are
pure passthrough integers (epoch seconds) rather than a derived quantity, so normalizing
them here introduces no second opinion about market values.
"""

from __future__ import annotations

from datetime import datetime

from app.bus.protocol import DEPTH_LEVELS, DepthLevel, NormTick
from app.kite.ticks import depth_side, field_int, ohlc_paise, to_paise


def epoch_seconds(value: object) -> int:
    """Coerce a Kite timestamp to integer epoch seconds; ``0`` when absent or unusable."""
    if value is None:
        return 0
    if isinstance(value, datetime):
        return max(0, int(value.timestamp()))
    if isinstance(value, int | float):
        return max(0, int(value))
    try:
        return max(0, int(str(value)))
    except (TypeError, ValueError):
        return 0


def normalize_tick(tick: dict) -> NormTick:
    """Build a :class:`NormTick` from a ``full``-mode Kite tick dict.

    Raises the same way the capture path does for genuinely uncoercible input, so a tick
    the ingestion contract rejects is never published as valid market data.
    """
    open_p, high_p, low_p, close_p = ohlc_paise(tick)
    bids = tuple(
        DepthLevel(price=price, quantity=quantity, orders=orders)
        for price, quantity, orders in depth_side(tick, "buy", DEPTH_LEVELS)
    )
    asks = tuple(
        DepthLevel(price=price, quantity=quantity, orders=orders)
        for price, quantity, orders in depth_side(tick, "sell", DEPTH_LEVELS)
    )
    return NormTick(
        instrument_token=field_int(tick, "instrument_token"),
        last_price=to_paise(tick.get("last_price")),
        average_traded_price=to_paise(tick.get("average_traded_price")),
        volume_traded=field_int(tick, "volume_traded"),
        total_buy_quantity=field_int(tick, "total_buy_quantity"),
        total_sell_quantity=field_int(tick, "total_sell_quantity"),
        oi=field_int(tick, "oi"),
        oi_day_high=field_int(tick, "oi_day_high"),
        oi_day_low=field_int(tick, "oi_day_low"),
        ohlc_open=open_p,
        ohlc_high=high_p,
        ohlc_low=low_p,
        ohlc_close=close_p,
        exchange_timestamp=epoch_seconds(tick.get("exchange_timestamp")),
        last_trade_time=epoch_seconds(tick.get("last_trade_time")),
        bids=bids,
        asks=asks,
    )
