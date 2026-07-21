"""Historical request model + validation guards (ported from kite_broker)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.historical.intervals import get_interval

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SELECTION_FULL_CHAIN = "full_chain"
SELECTION_ATM_WINDOW = "atm_window"

EXPIRY_SPECIFIC = "specific"
EXPIRY_NEAREST_WEEKLY = "nearest_weekly"
EXPIRY_NEAREST_MONTHLY = "nearest_monthly"
EXPIRY_ALL = "all_expiries"


@dataclass
class HistoricalRequest:
    underlying: str
    interval: str
    from_date: date
    to_date: date
    selection_mode: str = SELECTION_FULL_CHAIN
    expiry_mode: str = EXPIRY_SPECIFIC
    expiry: str | None = None
    strike_range: tuple[int, int] | None = None  # explicit (low, high) rupees
    weekly_only: bool = False
    monthly_only: bool = False
    chunk_size_days: int | None = None
    strikes_per_side: int = 50
    tokens: list[int] = field(default_factory=list)

    def validate(self) -> None:
        interval = get_interval(self.interval)  # raises on unknown
        if self.from_date >= self.to_date:
            raise ValueError("from_date must be strictly before to_date")
        span_days = (self.to_date - self.from_date).days
        if span_days > interval.max_ui_days:
            raise ValueError(
                f"span {span_days}d exceeds {self.interval} max_ui_days {interval.max_ui_days}"
            )
        if self.weekly_only and self.monthly_only:
            raise ValueError("weekly_only and monthly_only are mutually exclusive")
        if self.selection_mode == SELECTION_ATM_WINDOW and self.strike_range is not None:
            raise ValueError("atm_window and an explicit strike_range are mutually exclusive")
        if self.selection_mode not in (SELECTION_FULL_CHAIN, SELECTION_ATM_WINDOW):
            raise ValueError(f"invalid selection_mode '{self.selection_mode}'")
        if self.expiry_mode == EXPIRY_SPECIFIC:
            if not self.expiry or not _ISO_DATE.match(self.expiry):
                raise ValueError("expiry must be 'YYYY-MM-DD' for specific expiry_mode")
        if self.strike_range is not None and self.strike_range[0] >= self.strike_range[1]:
            raise ValueError("strike_range low must be < high")
