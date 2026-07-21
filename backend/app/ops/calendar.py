"""Trading calendar + market-hours logic.

The exchange runs in IST; frames store Unix epoch **ms UTC**, but file dates and the
session window use the **IST trading date** (docs/60-operations/operations-runbook.md).
Regular session 09:15-15:30 IST; weekends and configured holidays are non-trading.

India has no DST, so we fall back to a fixed +05:30 offset if the tz database is not
available in the environment.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

IST_FALLBACK = timezone(timedelta(hours=5, minutes=30))

# Phases
PHASE_HOLIDAY = "HOLIDAY"
PHASE_PRE_OPEN = "PRE_OPEN"
PHASE_OPEN = "OPEN"
PHASE_CLOSED = "CLOSED"


def _get_tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - missing tzdata -> fixed offset for IST
        if name in ("Asia/Kolkata", "Asia/Calcutta"):
            return IST_FALLBACK
        return UTC


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class TradingCalendar:
    """Answers trading-date, is-open, and session-phase questions from an epoch ms."""

    def __init__(
        self,
        holidays: set[str] | None = None,
        timezone_name: str = "Asia/Kolkata",
        market_open: str = "09:15",
        market_close: str = "15:30",
    ) -> None:
        self.holidays = set(holidays or set())
        self.tz = _get_tz(timezone_name)
        self.market_open = _parse_hhmm(market_open)
        self.market_close = _parse_hhmm(market_close)

    def local_dt(self, now_ms: int) -> datetime:
        return datetime.fromtimestamp(now_ms / 1000, tz=UTC).astimezone(self.tz)

    def trading_date(self, now_ms: int) -> str:
        """IST trading date string 'YYYY-MM-DD' used for file names."""
        return self.local_dt(now_ms).strftime("%Y-%m-%d")

    def is_trading_day(self, now_ms: int) -> bool:
        dt = self.local_dt(now_ms)
        if dt.weekday() >= 5:  # 5=Sat, 6=Sun
            return False
        return dt.strftime("%Y-%m-%d") not in self.holidays

    def phase(self, now_ms: int) -> str:
        if not self.is_trading_day(now_ms):
            return PHASE_HOLIDAY
        t = self.local_dt(now_ms).time()
        if t < self.market_open:
            return PHASE_PRE_OPEN
        if t <= self.market_close:
            return PHASE_OPEN
        return PHASE_CLOSED

    def is_open(self, now_ms: int) -> bool:
        return self.phase(now_ms) == PHASE_OPEN
