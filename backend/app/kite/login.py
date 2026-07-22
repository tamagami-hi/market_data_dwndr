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
always entered by the user through the terminal or staged frontend flow.

Static IP (Kite requires a whitelisted static IP for API calls from Apr 2026): the
outbound HTTP client can bind a source address (``KITE_STATIC_IP``) and/or route
through a proxy (``KITE_HTTP_PROXY``), so on a static-IP host all Kite calls egress
from that IP.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import math
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlparse

from app.kite.auth import auth_header, compute_checksum
from app.kite.external_rate import resolve_daily_risk_free_rate
from app.kite.external_token import ExternalTokenError, fetch_external_access_token
from app.session import SessionState, load_session, now_ms, save_session

KITE_LOGIN_BASE = "https://kite.zerodha.com"
KITE_API_BASE = "https://api.kite.trade"
_USER_AGENT = "market_data_dwndr/0.1 (+automated-login)"
_KITE_HOST = "kite.zerodha.com"
_TOTP_PATTERN = re.compile(r"^[0-9]{6}$")
logger = logging.getLogger(__name__)


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


def prompt_totp() -> str:
    """Read the 6-digit TOTP interactively from the terminal."""
    code = input("Enter 6-digit Kite TOTP: ").strip()
    if not code:
        raise KiteLoginError("no TOTP entered")
    return code


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


@dataclass(frozen=True)
class PasswordChallenge:
    """Opaque Kite password-login result needed for the TOTP step."""

    request_id: str
    twofa_type: str


def begin_login(client: HttpClient, user_id: str, password: str) -> PasswordChallenge:
    """Submit env-backed credentials and return the server's TOTP challenge."""
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
    return PasswordChallenge(
        request_id=request_id,
        twofa_type=login_data.get("twofa_type") or "totp",
    )


def complete_totp(
    client: HttpClient,
    api_key: str,
    user_id: str,
    challenge: PasswordChallenge,
    totp: str,
    *,
    max_redirects: int = 10,
) -> str:
    """Submit a user-entered TOTP and return the resulting request token."""
    if not _TOTP_PATTERN.fullmatch(totp):
        raise KiteLoginError("TOTP must contain exactly 6 ASCII digits")

    _require_success(
        client.post(
            f"{KITE_LOGIN_BASE}/api/twofa",
            data={
                "user_id": user_id,
                "request_id": challenge.request_id,
                "twofa_value": totp,
                "twofa_type": challenge.twofa_type,
            },
        ),
        "twofa",
    )

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
            if urlparse(next_url).hostname != _KITE_HOST:
                raise KiteLoginError(
                    "connect/login redirected off-site without a request_token "
                    "(check api_key / redirect URL configuration)"
                )
            url = next_url
            continue
        raise KiteLoginError(
            f"connect/login did not redirect (status {resp.status_code}); "
            "the app may need one-time authorization in a browser first"
        )
    raise KiteLoginError("exceeded redirect limit while resolving request_token")


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
    challenge = begin_login(client, user_id, password)
    return complete_totp(
        client,
        api_key,
        user_id,
        challenge,
        totp_provider(),
        max_redirects=max_redirects,
    )


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


def validate_access_token(
    client: HttpClient,
    api_key: str,
    access_token: str,
    *,
    expected_user_id: str | None = None,
) -> None:
    """Verify that a broker token works with this API key and expected user."""
    profile = _require_success(
        client.get(
            f"{KITE_API_BASE}/user/profile",
            headers=auth_header(api_key, access_token),
        ),
        "external access token validation",
    )
    user_id = profile.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise KiteLoginError("external access token profile has no user id")
    if expected_user_id and user_id != expected_user_id:
        raise KiteLoginError("external access token belongs to a different user")


def validate_risk_free_rate(value: float) -> float:
    """Return a plausible decimal risk-free rate or reject unsafe input."""
    rate = float(value)
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise KiteLoginError(
            "risk_free_rate must be a decimal between 0 and 1"
        )
    return rate


def _persist_session(
    settings, trading_date: str, access_token: str, risk_free_rate: float
) -> SessionState:
    timestamp = now_ms()
    state = SessionState(
        trading_date=trading_date,
        access_token=access_token,
        risk_free_rate=float(risk_free_rate),
        access_token_at=timestamp,
        started_at=timestamp,
    )
    save_session(settings.state_dir, state)
    return state


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
            "risk_free_rate is required; set RISK_FREE_RATE or pass it in"
        )
    rate = validate_risk_free_rate(rate)

    provider = totp_provider or prompt_totp
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

    return _persist_session(settings, trading_date, access_token, rate)


def run_interactive_login(
    settings,
    *,
    trading_date: str,
    risk_free_rate: float | None = None,
    client: HttpClient | None = None,
    external_token_fetcher: Callable[[], str | None] | None = None,
    external_token_validator: Callable[[str], None] | None = None,
) -> SessionState:
    """Check the VPS token, then fall back to credentials, TOTP, and rate."""
    if external_token_fetcher is None:

        def configured_token_fetcher() -> str | None:
            return fetch_external_access_token(settings)

        token_fetcher = configured_token_fetcher
    else:
        token_fetcher = external_token_fetcher

    try:
        external_access_token = token_fetcher()
    except ExternalTokenError as exc:
        raise KiteLoginError(str(exc)) from exc

    if external_access_token:
        validation_client = None
        try:
            if external_token_validator is None:
                validation_client = build_kite_http_client(
                    settings.kite_static_ip,
                    settings.kite_http_proxy,
                )
                validate_access_token(
                    validation_client,
                    settings.kite_api_key,
                    external_access_token,
                    expected_user_id=settings.kite_user_id,
                )
            else:
                external_token_validator(external_access_token)
        except Exception as exc:
            raise KiteLoginError("external token service returned an unusable token") from exc
        finally:
            if validation_client is not None:
                try:
                    validation_client.close()
                except Exception:  # noqa: BLE001
                    logger.exception("failed to close external-token validation client")
        rate = _resolve_interactive_rate(risk_free_rate)
        return _persist_session(settings, trading_date, external_access_token, rate)

    if not settings.kite_user_id or not settings.kite_password:
        raise KiteLoginError(
            "KITE_USER_ID and KITE_PASSWORD must be set in the environment to log in"
        )

    owns_client = client is None
    http = client or build_kite_http_client(settings.kite_static_ip, settings.kite_http_proxy)
    try:
        challenge = begin_login(http, settings.kite_user_id, settings.kite_password)
        request_token = complete_totp(
            http,
            settings.kite_api_key,
            settings.kite_user_id,
            challenge,
            prompt_totp(),
        )
        rate = _resolve_interactive_rate(risk_free_rate)
        access_token = exchange_request_token(
            http, settings.kite_api_key, settings.kite_api_secret, request_token
        )
    finally:
        if owns_client:
            http.close()

    return _persist_session(settings, trading_date, access_token, rate)


def _resolve_interactive_rate(risk_free_rate: float | None) -> float:
    rate = risk_free_rate
    if rate is None:
        try:
            rate = float(input("Enter risk-free rate (decimal, e.g. 0.0691): ").strip())
        except (ValueError, EOFError) as exc:
            raise KiteLoginError("invalid risk-free rate") from exc
    return validate_risk_free_rate(rate)


def _persist_credentials_session(
    settings, trading_date: str, access_token: str, risk_free_rate: float | None
) -> SessionState:
    """Persist a credentials-login session; the rate is resolved (not prompted)."""
    timestamp = now_ms()
    validated = validate_risk_free_rate(risk_free_rate) if risk_free_rate is not None else None
    state = SessionState(
        trading_date=trading_date,
        access_token=access_token,
        risk_free_rate=validated,
        access_token_at=timestamp,
        started_at=timestamp,
        risk_free_rate_as_of=trading_date if validated is not None else None,
    )
    save_session(settings.state_dir, state)
    return state


def run_credentials_login(
    settings,
    *,
    trading_date: str,
    user_id: str,
    password: str,
    api_key: str,
    api_secret: str,
    totp_provider: TotpProvider,
    client: HttpClient | None = None,
    rate_resolver: Callable[[], float | None] | None = None,
) -> SessionState:
    """Credentials + mandatory TOTP login (no external token), then persist.

    Used by the ``md-login`` CLI. The risk-free rate is resolved from the calspread
    broker (env fallback), never prompted. Kite's TOTP is a required second factor of
    every credentials login, so ``totp_provider`` is always invoked.
    """
    owns_client = client is None
    http = client or build_kite_http_client(settings.kite_static_ip, settings.kite_http_proxy)
    try:
        challenge = begin_login(http, user_id, password)
        request_token = complete_totp(http, api_key, user_id, challenge, totp_provider())
        access_token = exchange_request_token(http, api_key, api_secret, request_token)
    finally:
        if owns_client:
            http.close()

    resolve = rate_resolver or (lambda: resolve_daily_risk_free_rate(settings))
    return _persist_credentials_session(settings, trading_date, access_token, resolve())


# --------------------------------------------------------------------------- #
# CLI entrypoint (md-login)
# --------------------------------------------------------------------------- #


def _resolve_trading_date(settings) -> str:
    from app.ops.calendar import TradingCalendar

    cal = TradingCalendar(
        holidays=set(getattr(settings, "market_holidays", [])),
        timezone_name=settings.timezone,
        market_open=settings.market_open,
        market_close=settings.market_close,
    )
    return cal.trading_date(now_ms())


def _prompt_credentials_from_terminal() -> dict[str, str]:
    """Read all four credentials from the TTY; secrets via getpass (no echo/history)."""
    try:
        user_id = input("Kite user id: ").strip()
        password = getpass.getpass("Kite password: ").strip()
        api_key = input("Kite API key: ").strip()
        api_secret = getpass.getpass("Kite API secret: ").strip()
    except EOFError as exc:
        raise KiteLoginError("credentials must be entered on an interactive terminal") from exc
    if not all((user_id, password, api_key, api_secret)):
        raise KiteLoginError("all four credentials are required")
    return {
        "user_id": user_id,
        "password": password,
        "api_key": api_key,
        "api_secret": api_secret,
    }


def _prompt_totp_hidden() -> str:
    """Read the 6-digit TOTP from the TTY without echo."""
    try:
        code = getpass.getpass("Kite TOTP (6 digits): ").strip()
    except EOFError as exc:
        raise KiteLoginError("TOTP must be entered on an interactive terminal") from exc
    if not code:
        raise KiteLoginError("no TOTP entered")
    return code


def main(argv: list[str] | None = None) -> int:
    """Console entrypoint (docker exec -it): credentials + mandatory TOTP login.

    Default: use the four env-seeded credentials. ``--manual``: prompt for all four
    credentials on the terminal (nothing is written back to the env). Either way Kite's
    TOTP is entered on the terminal. A valid session for today hard-blocks the login
    (mirroring the automated fetcher) unless ``--force`` is given.
    """
    parser = argparse.ArgumentParser(prog="md-login", description="Kite credentials + TOTP login")
    parser.add_argument("--date", help="Trading date YYYY-MM-DD (default: today IST)")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Prompt for all four credentials on the terminal instead of using the env",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Log in even if a valid session for today already exists",
    )
    args = parser.parse_args(argv)

    from app.config import get_settings

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    trading_date = args.date or _resolve_trading_date(settings)

    # Hard block: once a valid session exists, both the manual login and the automated
    # fetcher stand down until it becomes invalid.
    existing = load_session(settings.state_dir, trading_date)
    if existing is not None and existing.access_token and not args.force:
        print(
            f"already authenticated for {trading_date}; session is valid. Nothing to do. "
            "(use --force to log in again)"
        )
        return 0

    if args.manual:
        creds = _prompt_credentials_from_terminal()
    else:
        if not settings.kite_user_id or not settings.kite_password:
            print(
                "KITE_USER_ID and KITE_PASSWORD are not set; use `md-login --manual` to "
                "enter credentials on the terminal.",
                file=sys.stderr,
            )
            return 2
        creds = {
            "user_id": settings.kite_user_id,
            "password": settings.kite_password,
            "api_key": settings.kite_api_key,
            "api_secret": settings.kite_api_secret,
        }

    try:
        state = run_credentials_login(
            settings,
            trading_date=trading_date,
            totp_provider=_prompt_totp_hidden,
            **creds,
        )
    except KiteLoginError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1

    rate_txt = state.risk_free_rate if state.risk_free_rate is not None else "unset (rate broker + env fallback both unavailable)"
    print(
        f"login OK for {trading_date}: risk_free_rate={rate_txt}. "
        "Session saved under _state/."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
