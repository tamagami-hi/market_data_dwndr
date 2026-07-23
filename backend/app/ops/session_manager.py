"""Daily session orchestration: login once, resume on restart.

Ties the trading calendar (today's IST date) to the authenticator + session-state
store (docs/60-operations/session-state.md). On the first start of the day the user
logs in (request_token + risk-free rate); on a mid-day restart the same session is resumed
with no re-prompt, and capture appends to today's files (headers are written only when
a file is empty, so no duplicate header).
"""

from __future__ import annotations

from app.kite.auth import KiteAuthenticator
from app.ops.calendar import TradingCalendar
from app.session import SessionState, now_ms


class SessionManager:
    def __init__(
        self,
        authenticator: KiteAuthenticator,
        calendar: TradingCalendar,
        *,
        clock=now_ms,
    ) -> None:
        self.authenticator = authenticator
        self.calendar = calendar
        self._clock = clock

    def current_trading_date(self) -> str:
        return self.calendar.trading_date(self._clock())

    def active_session(self) -> SessionState | None:
        """Today's persisted session, if any (used to decide resume vs. login)."""
        return self.authenticator.resume(self.current_trading_date())

    def resume_or_login(
        self,
        request_token: str | None = None,
        risk_free_rate: float | None = None,
    ) -> SessionState:
        """Resume today's session, or perform the login exchange if none exists."""
        return self.authenticator.get_or_login(
            self.current_trading_date(),
            request_token=request_token,
            risk_free_rate=risk_free_rate,
        )
