"""Session service: the app-level facade over daily login + session state.

Wraps the trading calendar, the (env-seeded) credentials, and the automated login so
the FastAPI layer and startup can ask two questions:

    * ``status()``  -- is today's session present? which market phase are we in?
    * ``login()``   -- run the automated login (TOTP supplied by the caller) or exchange
                       a ``request_token`` from the browser OAuth fallback.

The login callable is injected so the API can be tested without the network.
"""

from __future__ import annotations

from collections.abc import Callable

from app.kite.auth import KiteAuthenticator, login_url
from app.kite.login import run_login
from app.kite.login_flow import LoginCoordinator, LoginProgress
from app.ops.calendar import TradingCalendar
from app.session import SessionState, load_session, now_ms

LoginFn = Callable[..., SessionState]


class SessionService:
    def __init__(
        self,
        settings,
        *,
        login_fn: LoginFn = run_login,
        login_flow: LoginCoordinator | None = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.settings = settings
        self._login_fn = login_fn
        self._clock = clock
        self._login_flow = login_flow or LoginCoordinator(settings, clock=clock)
        self.calendar = TradingCalendar(
            timezone_name=settings.timezone,
            market_open=settings.market_open,
            market_close=settings.market_close,
        )

    def trading_date(self) -> str:
        return self.calendar.trading_date(self._clock())

    def active_session(self) -> SessionState | None:
        return load_session(self.settings.state_dir, self.trading_date())

    @property
    def credentials_present(self) -> bool:
        return bool(self.settings.kite_user_id and self.settings.kite_password)

    @property
    def external_token_source_configured(self) -> bool:
        return bool(
            getattr(self.settings, "kite_token_broker_url", None)
            and getattr(self.settings, "kite_token_broker_passcode", None)
        )

    def status(self) -> dict:
        """A JSON-friendly snapshot of auth/session state (no secrets)."""
        now = self._clock()
        session = self.active_session()
        return {
            "configured": True,
            "authenticated": session is not None,
            "trading_date": self.trading_date(),
            "market_phase": self.calendar.phase(now),
            "credentials_present": self.credentials_present,
            "external_token_source_configured": self.external_token_source_configured,
            "static_ip_configured": bool(
                self.settings.kite_static_ip or self.settings.kite_http_proxy
            ),
            "risk_free_rate": session.risk_free_rate if session else None,
            "access_token_at": session.access_token_at if session else None,
        }

    def login(
        self,
        *,
        totp: str | None = None,
        request_token: str | None = None,
        risk_free_rate: float | None = None,
    ) -> SessionState:
        """Log in for today's trading date.

        - ``request_token`` set  -> browser OAuth fallback: just exchange it.
        - otherwise              -> automated login with a caller-supplied TOTP.
        """
        trading_date = self.trading_date()
        rate = risk_free_rate if risk_free_rate is not None else self.settings.risk_free_rate

        if request_token:
            if rate is None:
                raise ValueError("risk_free_rate is required to complete login")
            authenticator = KiteAuthenticator(
                self.settings.kite_api_key, self.settings.kite_api_secret, self.settings.state_dir
            )
            return authenticator.authenticate(request_token, float(rate), trading_date)

        if not totp:
            raise ValueError("TOTP must be entered by the user")

        def provider() -> str:
            return totp

        return self._login_fn(
            self.settings,
            trading_date=trading_date,
            risk_free_rate=rate,
            totp_provider=provider,
        )

    def login_url(self) -> str:
        """Browser OAuth fallback URL (manual login on Zerodha)."""
        return login_url(self.settings.kite_api_key)

    def start_login(self) -> LoginProgress:
        return self._login_flow.start(self.trading_date())

    def submit_login_totp(self, attempt_id: str, totp: str) -> LoginProgress:
        return self._login_flow.submit_totp(attempt_id, totp)

    def complete_login(self, attempt_id: str, risk_free_rate: float) -> SessionState:
        return self._login_flow.complete(attempt_id, risk_free_rate)

    def cancel_login(self, attempt_id: str) -> None:
        self._login_flow.cancel(attempt_id)

    def close(self) -> None:
        self._login_flow.close()
