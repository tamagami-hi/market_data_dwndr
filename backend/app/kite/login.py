"""Automated Kite login (seeded from env) with terminal TOTP.

algo_engine uses the *browser* OAuth flow: it redirects the user to
``kite.zerodha.com/connect/login``, the user completes login + TOTP on Zerodha, and
Kite calls back with a ``request_token`` which is exchanged for an ``access_token``
(SHA-256 checksum) -- see ``zerodha_oauth/{automated_login,server_callbacks}.rs``.

Here we automate the whole thing headlessly so a single ``md-login`` run gets a token:

    1. POST /api/login   {user_id, password}            -> request_id
    2. POST /api/twofa   {user_id, request_id, TOTP}     -> session cookies
    3. GET  /connect/login?v=3&api_key=...  (follow)     -> redirect w/ request_token
    4. POST api.kite.trade/session/token {checksum}      -> access_token

Credentials are seeded from the environment (see ``config.Settings``); the TOTP is
generated from ``KITE_TOTP_SECRET`` if set, otherwise prompted from the terminal.

Static IP (Kite requires a whitelisted static IP for API calls from Apr 2026): the
outbound HTTP client can bind a source address (``KITE_STATIC_IP``) and/or route
through a proxy (``KITE_HTTP_PROXY``), so on a static-IP host all Kite calls egress
from that IP.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlparse

from app.kite.auth import compute_checksum
from app.session import SessionState, now_ms, save_session

KITE_LOGIN_BASE = "https://kite.zerodha.com"
KITE_API_BASE = "https://api.kite.trade"
_USER_AGENT = "market_data_dwndr/0.1 (+automated-login)"
_KITE_HOST = "kite.zerodha.com"


class KiteLoginError(Exception):
    """Raised when any step of the automated login fails."""


# --------------------------------------------------------------------------- #
# HTTP client (static-IP / proxy aware)
# --------------------------------------------------------------------------- #


class HttpResponse(Protocol):
    status_code: int
    text: str

    @property
    def headers(self) -> object: ...

    def json(self) -> dict: ...


class HttpClient(Protocol):
    def get(self, url: str, headers: dict | None = ...) -> HttpResponse: ...
    def post(
        self, url: str, data: dict | None = ..., headers: dict | None = ...
    ) -> HttpResponse: ...
    def close(self) -> None: ...


def build_kite_http_client(
    static_ip: str | None = None,
    proxy: str | None = None,
    timeout: float = 30.0,
):
    """Build an ``httpx.Client`` that binds the static IP / uses a proxy for egress.

    ``follow_redirects=False`` -- we walk the ``connect/login`` redirect chain by hand
    to capture the ``request_token`` from the ``Location`` header.
    """
    import httpx

    kwargs: dict = {
        "follow_redirects": False,
        "timeout": timeout,
        "headers": {"User-Agent": _USER_AGENT},
    }
    if proxy:
        kwargs["proxy"] = proxy
    elif static_ip:
        kwargs["transport"] = httpx.HTTPTransport(local_address=static_ip)
    return httpx.Client(**kwargs)


# --------------------------------------------------------------------------- #
# TOTP providers
# --------------------------------------------------------------------------- #

TotpProvider = Callable[[], str]


def totp_from_secret(secret: str) -> str:
    """Current 6-digit TOTP from a base32 secret (pyotp)."""
    import pyotp

    return pyotp.TOTP(secret).now()


def prompt_totp() -> str:
    """Read the 6-digit TOTP interactively from the terminal."""
    code = input("Enter 6-digit Kite TOTP: ").strip()
    if not code:
        raise KiteLoginError("no TOTP entered")
    return code


def make_totp_provider(totp_secret: str | None) -> TotpProvider:
    """Auto-generate from a secret if provided, else prompt the terminal."""
    if totp_secret:
        return lambda: totp_from_secret(totp_secret)
    return prompt_totp


# --------------------------------------------------------------------------- #
# Response helpers
# --------------------------------------------------------------------------- #


def _require_success(resp: HttpResponse, step: str) -> dict:
    """Parse a Kite JSON response and return ``data``, raising on any error."""
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise KiteLoginError(f"{step}: non-JSON response ({resp.status_code})") from exc
    if body.get("status") != "success":
        message = body.get("message") or body.get("error_type") or "unknown error"
        raise KiteLoginError(f"{step} failed: {message}")
    return body.get("data") or {}


def _extract_request_token(location: str | None) -> str | None:
    if not location:
        return None
    tokens = parse_qs(urlparse(location).query).get("request_token")
    return tokens[0] if tokens else None


# --------------------------------------------------------------------------- #
# Flow steps
# --------------------------------------------------------------------------- #


def fetch_request_token(
    client: HttpClient,
    api_key: str,
    user_id: str,
    password: str,
    totp_provider: TotpProvider,
    *,
    max_redirects: int = 10,
) -> str:
    """Drive login -> twofa -> connect/login and return the ``request_token``."""
    # 1. password login
    login_data = _require_success(
        client.post(
            f"{KITE_LOGIN_BASE}/api/login",
            data={"user_id": user_id, "password": password},
        ),
        "login",
    )
    request_id = login_data.get("request_id")
    if not request_id:
        raise KiteLoginError("login succeeded but no request_id was returned")
    twofa_type = login_data.get("twofa_type") or "totp"

    # 2. two-factor (TOTP)
    code = totp_provider()
    _require_success(
        client.post(
            f"{KITE_LOGIN_BASE}/api/twofa",
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": code,
                "twofa_type": twofa_type,
            },
        ),
        "twofa",
    )

    # 3. connect/login -> follow the redirect chain until request_token appears
    url = f"{KITE_LOGIN_BASE}/connect/login?v=3&api_key={api_key}"
    for _ in range(max_redirects):
        resp = client.get(url)
        location = resp.headers.get("location") if hasattr(resp.headers, "get") else None
        if 300 <= resp.status_code < 400:
            token = _extract_request_token(location)
            if token:
                return token
            if not location:
                raise KiteLoginError("redirect without a Location header during connect/login")
            next_url = urljoin(url, location)
            # Follow redirects that stay on the Kite domain; an external redirect that
            # carried no request_token means the flow did not complete.
            if urlparse(next_url).hostname != _KITE_HOST:
                raise KiteLoginError(
                    "connect/login redirected off-site without a request_token "
                    "(check api_key / redirect URL configuration)"
                )
            url = next_url
            continue
        # A 200 here usually means an interstitial (e.g. authorize app) we can't
        # complete headlessly, or invalid credentials.
        raise KiteLoginError(
            f"connect/login did not redirect (status {resp.status_code}); "
            "the app may need one-time authorization in a browser first"
        )
    raise KiteLoginError("exceeded redirect limit while resolving request_token")


def exchange_request_token(
    client: HttpClient,
    api_key: str,
    api_secret: str,
    request_token: str,
) -> str:
    """Exchange a ``request_token`` for an ``access_token`` (checksum-signed)."""
    checksum = compute_checksum(api_key, request_token, api_secret)
    data = _require_success(
        client.post(
            f"{KITE_API_BASE}/session/token",
            data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
            headers={"X-Kite-Version": "3"},
        ),
        "session token exchange",
    )
    access_token = data.get("access_token")
    if not access_token:
        raise KiteLoginError("token exchange succeeded but no access_token was returned")
    return access_token


def run_login(
    settings,
    *,
    trading_date: str,
    risk_free_rate: float | None = None,
    totp_provider: TotpProvider | None = None,
    client: HttpClient | None = None,
) -> SessionState:
    """Full automated login; persists and returns the day's :class:`SessionState`."""
    if not settings.kite_user_id or not settings.kite_password:
        raise KiteLoginError(
            "KITE_USER_ID and KITE_PASSWORD must be set in the environment to log in"
        )
    rate = risk_free_rate if risk_free_rate is not None else settings.risk_free_rate
    if rate is None:
        raise KiteLoginError(
            "risk_free_rate (10-yr bond yield) is required; set RISK_FREE_RATE or pass it in"
        )

    provider = totp_provider or make_totp_provider(settings.kite_totp_secret)
    owns_client = client is None
    http = client or build_kite_http_client(settings.kite_static_ip, settings.kite_http_proxy)
    try:
        request_token = fetch_request_token(
            http, settings.kite_api_key, settings.kite_user_id, settings.kite_password, provider
        )
        access_token = exchange_request_token(
            http, settings.kite_api_key, settings.kite_api_secret, request_token
        )
    finally:
        if owns_client:
            http.close()

    ts = now_ms()
    state = SessionState(
        trading_date=trading_date,
        access_token=access_token,
        risk_free_rate=float(rate),
        access_token_at=ts,
        started_at=ts,
    )
    save_session(settings.state_dir, state)
    return state


# --------------------------------------------------------------------------- #
# CLI entrypoint (md-login)
# --------------------------------------------------------------------------- #


def _resolve_trading_date(settings) -> str:
    from app.ops.calendar import TradingCalendar

    cal = TradingCalendar(
        timezone_name=settings.timezone,
        market_open=settings.market_open,
        market_close=settings.market_close,
    )
    return cal.trading_date(now_ms())


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint: seed creds from env, take TOTP from the terminal, log in."""
    parser = argparse.ArgumentParser(prog="md-login", description="Automated Kite login")
    parser.add_argument("--date", help="Trading date YYYY-MM-DD (default: today IST)")
    parser.add_argument(
        "--rate",
        type=float,
        help="10-yr bond yield as a decimal (e.g. 0.0691); prompted if unset",
    )
    args = parser.parse_args(argv)

    from app.config import get_settings

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    trading_date = args.date or _resolve_trading_date(settings)

    rate = args.rate if args.rate is not None else settings.risk_free_rate
    if rate is None:
        try:
            rate = float(input("Enter 10-yr bond yield (decimal, e.g. 0.0691): ").strip())
        except (ValueError, EOFError):
            print("invalid bond yield", file=sys.stderr)
            return 2

    try:
        state = run_login(settings, trading_date=trading_date, risk_free_rate=rate)
    except KiteLoginError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1

    masked = f"{state.access_token[:4]}…{state.access_token[-4:]}" if state.access_token else ""
    print(
        f"login OK for {trading_date}: access_token={masked}, "
        f"bond_yield={state.risk_free_rate}. Session saved under _state/."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
