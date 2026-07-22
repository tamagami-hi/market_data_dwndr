"""Kite daily login flow.

Kite issues a fresh ``access_token`` each day (resets ~06:00 IST). The morning flow:

1. User opens the login URL and authorizes on Kite.
2. Kite redirects back with a ``request_token``.
3. We exchange ``request_token`` -> ``access_token`` using the API secret
   (checksum = SHA-256(api_key + request_token + api_secret)).
4. The risk-free rate is entered on the same screen.
5. Both are persisted to session state and reused on restart.

The token exchange is injected (``SessionGenerator``) so it can be unit-tested
without the network; the default uses the ``kiteconnect`` SDK.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Protocol

from app.session import SessionState, load_session, now_ms, save_session

LOGIN_URL_TEMPLATE = "https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"


def login_url(api_key: str) -> str:
    """Return the Kite Connect login URL the user opens each morning."""
    return LOGIN_URL_TEMPLATE.format(api_key=api_key)


def compute_checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """SHA-256 checksum Kite requires for the token exchange."""
    raw = (api_key + request_token + api_secret).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def auth_header(api_key: str, access_token: str) -> dict[str, str]:
    """Authorization + version headers for Kite REST calls."""
    return {
        "X-Kite-Version": "3",
        "Authorization": f"token {api_key}:{access_token}",
    }


class SessionGenerator(Protocol):
    """Exchanges a request_token for a session dict containing ``access_token``."""

    def __call__(self, request_token: str, api_secret: str) -> dict: ...


def _default_session_generator(api_key: str) -> SessionGenerator:
    """Real generator backed by the ``kiteconnect`` SDK."""

    def _generate(request_token: str, api_secret: str) -> dict:
        from kiteconnect import KiteConnect  # imported lazily; optional at test time

        kite = KiteConnect(api_key=api_key)
        return kite.generate_session(request_token, api_secret=api_secret)

    return _generate


class KiteAuthenticator:
    """Runs the login exchange and persists the day's session state."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        state_dir,
        session_generator: SessionGenerator | None = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.state_dir = state_dir
        self._generate = session_generator or _default_session_generator(api_key)
        self._clock = clock

    def login_url(self) -> str:
        return login_url(self.api_key)

    def resume(self, trading_date: str) -> SessionState | None:
        """Return an existing valid session for ``trading_date`` (no re-prompt)."""
        return load_session(self.state_dir, trading_date)

    def authenticate(
        self,
        request_token: str,
        risk_free_rate: float,
        trading_date: str,
    ) -> SessionState:
        """Exchange ``request_token`` for an access_token and persist session state."""
        result = self._generate(request_token, self.api_secret)
        access_token = result["access_token"]
        ts = self._clock()
        state = SessionState(
            trading_date=trading_date,
            access_token=access_token,
            risk_free_rate=risk_free_rate,
            access_token_at=ts,
            started_at=ts,
        )
        save_session(self.state_dir, state)
        return state

    def get_or_login(
        self,
        trading_date: str,
        request_token: str | None = None,
        risk_free_rate: float | None = None,
    ) -> SessionState:
        """Reuse today's session if present; otherwise perform the login exchange."""
        existing = self.resume(trading_date)
        if existing is not None:
            return existing
        if request_token is None or risk_free_rate is None:
            raise RuntimeError(
                "no session for today; request_token and risk_free_rate are required to log in"
            )
        return self.authenticate(request_token, risk_free_rate, trading_date)
