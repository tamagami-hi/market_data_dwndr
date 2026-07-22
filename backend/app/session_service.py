"""Session service: the app-level facade over daily login + session state.

Wraps the trading calendar, the (env-seeded) credentials, and the automated login so
the FastAPI layer and startup can ask two questions:

    * ``status()``  -- is today's session present? which market phase are we in?
    * ``login()``   -- run the automated login (TOTP supplied by the caller) or exchange
                       a ``request_token`` from the browser OAuth fallback.

The login callable is injected so the API can be tested without the network.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.kite.auth import KiteAuthenticator, login_url
from app.kite.external_token import fetch_external_access_token
from app.kite.login import (
    build_kite_http_client,
    run_login,
    validate_access_token,
    validate_risk_free_rate,
)
from app.kite.login_flow import LoginCoordinator, LoginProgress
from app.ops.calendar import TradingCalendar
from app.session import (
    SessionState,
    load_latest_session_before,
    load_session,
    now_ms,
    resolve_risk_free_rate,
    save_session,
)

logger = logging.getLogger(__name__)

LoginFn = Callable[..., SessionState]
BrokerFetcher = Callable[[], str | None]
BrokerValidator = Callable[[str], None]


class SessionService:
    def __init__(
        self,
        settings,
        *,
        login_fn: LoginFn = run_login,
        login_flow: LoginCoordinator | None = None,
        broker_fetcher: BrokerFetcher | None = None,
        broker_validator: BrokerValidator | None = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.settings = settings
        self._login_fn = login_fn
        self._clock = clock
        self._login_flow = login_flow or LoginCoordinator(settings, clock=clock)
        self._broker_fetcher = broker_fetcher or (lambda: fetch_external_access_token(settings))
        self._broker_validator = broker_validator or self._validate_broker_token
        self._previous_token_attempted_dates: frozenset[str] = frozenset()
        self._session_lock = threading.RLock()
        self.calendar = TradingCalendar(
            timezone_name=settings.timezone,
            market_open=settings.market_open,
            market_close=settings.market_close,
        )

    def trading_date(self) -> str:
        return self.calendar.trading_date(self._clock())

    def active_session(self) -> SessionState | None:
        return load_session(self.settings.state_dir, self.trading_date())

    def _validate_broker_token(self, access_token: str) -> None:
        client = build_kite_http_client(
            self.settings.kite_static_ip,
            self.settings.kite_http_proxy,
        )
        try:
            validate_access_token(
                client,
                self.settings.kite_api_key,
                access_token,
                expected_user_id=self.settings.kite_user_id,
            )
        finally:
            client.close()

    def acquire_broker_session(self) -> SessionState | None:
        """Reuse a valid prior token, otherwise fetch and validate the shared token."""
        with self._session_lock:
            return self._acquire_broker_session_unlocked()

    def _acquire_broker_session_unlocked(self) -> SessionState | None:
        existing = self.active_session()
        if existing is not None and existing.access_token:
            return existing

        trading_date = self.trading_date()
        access_token = self._validated_previous_token(trading_date)
        if access_token is None:
            access_token = self._broker_fetcher()
            if not access_token:
                return None
            self._broker_validator(access_token)

        rate = resolve_risk_free_rate(self.settings.state_dir, trading_date)
        if rate.risk_free_rate is None and self.settings.risk_free_rate is not None:
            risk_free_rate = validate_risk_free_rate(self.settings.risk_free_rate)
            rate_as_of = trading_date
            rate_update_required = False
        else:
            risk_free_rate = rate.risk_free_rate
            rate_as_of = rate.risk_free_rate_as_of
            rate_update_required = rate.rate_update_required
        timestamp = self._clock()
        state = SessionState(
            trading_date=trading_date,
            access_token=access_token,
            risk_free_rate=risk_free_rate,
            access_token_at=timestamp,
            started_at=timestamp,
            risk_free_rate_as_of=rate_as_of,
            rate_update_required=rate_update_required,
        )
        save_session(self.settings.state_dir, state)
        return state

    def _validated_previous_token(self, trading_date: str) -> str | None:
        if trading_date in self._previous_token_attempted_dates:
            return None
        self._previous_token_attempted_dates = (
            self._previous_token_attempted_dates | {trading_date}
        )
        previous = load_latest_session_before(self.settings.state_dir, trading_date)
        if previous is None or not previous.access_token:
            return None
        try:
            self._broker_validator(previous.access_token)
        except Exception as exc:  # noqa: BLE001 - rejected/expired token falls through
            logger.info("previous access token was not reusable (%s)", type(exc).__name__)
            return None
        return previous.access_token

    def update_risk_free_rate(self, risk_free_rate: float) -> SessionState:
        """Set today's operator-confirmed 10-year yield on an acquired session."""
        with self._session_lock:
            return self._update_risk_free_rate_unlocked(risk_free_rate)

    def _update_risk_free_rate_unlocked(self, risk_free_rate: float) -> SessionState:
        state = self.active_session()
        if state is None or not state.access_token:
            raise ValueError("a validated daily token is required before updating the rate")
        validated_rate = validate_risk_free_rate(risk_free_rate)
        if validated_rate != state.risk_free_rate and self._daily_bin_exists(
            self.trading_date()
        ):
            raise ValueError("the 10-year yield cannot change after today's BIN capture began")
        updated = replace(
            state,
            risk_free_rate=validated_rate,
            risk_free_rate_as_of=self.trading_date(),
            rate_update_required=False,
        )
        save_session(self.settings.state_dir, updated)
        return updated

    def _daily_bin_exists(self, trading_date: str) -> bool:
        live_root = Path(self.settings.state_dir).parent
        index_files = (live_root / "INDICES").glob(f"*/{trading_date}.bin")
        stock_file = live_root / "STOCKS" / f"{trading_date}.bin"
        return stock_file.exists() or any(index_files)

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
            "risk_free_rate_as_of": session.risk_free_rate_as_of if session else None,
            "rate_update_required": session.rate_update_required if session else False,
            "capture_ready": session.capture_ready if session else False,
            "access_token_at": session.access_token_at if session else None,
        }

    def login(
        self,
        *,
        totp: str | None = None,
        request_token: str | None = None,
        risk_free_rate: float | None = None,
    ) -> SessionState:
        with self._session_lock:
            return self._login_unlocked(
                totp=totp,
                request_token=request_token,
                risk_free_rate=risk_free_rate,
            )

    def _login_unlocked(
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
        existing = self.active_session()
        if existing is not None and existing.access_token:
            return existing
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
        with self._session_lock:
            existing = self.active_session()
            if existing is not None and existing.access_token:
                self._login_flow.cancel(attempt_id)
                if existing.rate_update_required or existing.risk_free_rate is None:
                    return self._update_risk_free_rate_unlocked(risk_free_rate)
                return existing
            return self._login_flow.complete(attempt_id, risk_free_rate)

    def cancel_login(self, attempt_id: str) -> None:
        self._login_flow.cancel(attempt_id)

    def close(self) -> None:
        self._login_flow.close()
