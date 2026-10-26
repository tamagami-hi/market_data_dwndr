"""Backend-only client for the Kite token held by the broker login service.

The service logs the Zerodha account in each morning and hands its token out at
``POST /api/v1/token``. A request passes Cloudflare Access with a service token
(``CF-Access-Client-Id`` / ``CF-Access-Client-Secret``) and the service's own
gate with ``{"client_id": KITE_USER_ID, "totp": <code from the token API seed>}``.
A code is accepted once, so a second fetch in the same 30 s step waits for the
next step and sends a fresh code.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import __version__
from app.kite.totp import STEP_SECONDS, TotpSeedError, code_for_step, decode_seed, step_at

MAX_BROKER_RESPONSE_BYTES = 8 * 1_024
# One try, then a fresh code for each step a code was already spent in.
MAX_ATTEMPTS = 3
# Sent past the step boundary, so the next code is surely current.
STEP_MARGIN_SECONDS = 1.0
EXPECTED_BROKER = "kite"
USER_AGENT = f"market-data-dwndr/{__version__}"
# Error codes the token API documents; anything else is reported as "unknown".
KNOWN_ERRORS = frozenset(
    {
        "bad_request",
        "unauthorized",
        "code_already_used",
        "locked",
        "rate_limited",
        "no_token_yet",
        "not_configured",
    }
)
_SETTING_NAMES = (
    "kite_token_broker_url",
    "kite_token_broker_cf_client_id",
    "kite_token_broker_cf_client_secret",
    "kite_token_broker_totp_seed",
)


class ExternalTokenError(Exception):
    """Raised when the token service cannot return a trustworthy result."""


class _TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    broker: str
    client_id: str
    access_token: Annotated[str, Field(min_length=1, max_length=2_048)]


class _ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: str | None = None


def token_broker_configured(settings) -> bool:
    return all(getattr(settings, name, None) is not None for name in _SETTING_NAMES)


def _build_client() -> httpx.Client:
    timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def fetch_external_access_token(
    settings,
    *,
    client=None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Return today's token, or ``None`` when the service has none yet."""
    values = [getattr(settings, name, None) for name in _SETTING_NAMES]
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ExternalTokenError("external token service configuration is incomplete")
    url, cf_client_id, cf_client_secret, seed_text = values
    user_id = (getattr(settings, "kite_user_id", None) or "").strip().upper()
    if not user_id:
        raise ExternalTokenError("external token service needs KITE_USER_ID")
    try:
        seed = decode_seed(seed_text.get_secret_value())
    except TotpSeedError as exc:
        raise ExternalTokenError("external token service TOTP seed is invalid") from exc

    headers = {
        "CF-Access-Client-Id": cf_client_id.get_secret_value(),
        "CF-Access-Client-Secret": cf_client_secret.get_secret_value(),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": USER_AGENT,
    }
    owns_client = client is None
    http = client or _build_client()
    try:
        spent_step: int | None = None
        for _ in range(MAX_ATTEMPTS):
            if spent_step is not None:
                sleep(_seconds_until_step(spent_step + 1, clock()))
            step = step_at(clock())
            body = json.dumps({"client_id": user_id, "totp": code_for_step(seed, step)})
            status_code, content = _post(http, str(url), headers, body.encode())
            if status_code == 200:
                return _token_from(content, user_id)
            error = _error_from(content)
            if status_code == 409 and error == "code_already_used":
                spent_step = step
                continue
            if status_code == 503 and error == "no_token_yet":
                return None
            raise ExternalTokenError(
                f"external token service refused the request (HTTP {status_code}, {error})"
            )
        raise ExternalTokenError("external token service rejected every code as already used")
    except ExternalTokenError:
        raise
    except (httpx.HTTPError, ValidationError, ValueError, TypeError) as exc:
        raise ExternalTokenError("external token service is unavailable or invalid") from exc
    finally:
        if owns_client:
            http.close()


def _seconds_until_step(step: int, now: float) -> float:
    wait = step * STEP_SECONDS - now + STEP_MARGIN_SECONDS
    return min(max(wait, 0.0), STEP_SECONDS + STEP_MARGIN_SECONDS)


def _post(http, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
    """POST and read a bounded, uncompressed body."""
    with http.stream("POST", url, headers=headers, content=body) as response:
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > MAX_BROKER_RESPONSE_BYTES:
            raise ExternalTokenError("external token service response is invalid")
        content_encoding = response.headers.get("content-encoding")
        if content_encoding and content_encoding.lower() != "identity":
            raise ExternalTokenError("external token service response is invalid")
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BROKER_RESPONSE_BYTES:
                raise ExternalTokenError("external token service response is invalid")
        return response.status_code, bytes(content)


def _token_from(content: bytes, user_id: str) -> str:
    payload = _TokenResponse.model_validate_json(content)
    if payload.broker != EXPECTED_BROKER or payload.client_id.upper() != user_id:
        raise ExternalTokenError("external token service returned another account's token")
    token = payload.access_token.strip()
    if not token:
        raise ExternalTokenError("external token service response is invalid")
    return token


def _error_from(content: bytes) -> str:
    try:
        error = _ErrorResponse.model_validate_json(content).error
    except ValidationError:
        return "unknown"
    return error if error in KNOWN_ERRORS else "unknown"
