"""Backend-only client for the daily risk-free rate from the calspread broker.

Mirrors ``external_token.py``: a hardened one-shot HTTPS GET that reuses the existing
``x-token-passcode`` (``kite_token_broker_passcode``). The endpoint returns the rate as a
**percent** (e.g. ``{"rf": 5.3324}`` == 5.3324%); we convert it to the decimal the
Greeks reconstruction expects (``0.053324``). The rate is fetched once per trading day
when the automated session is created; ``RISK_FREE_RATE`` in the env is the fallback.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

MAX_RATE_RESPONSE_BYTES = 4 * 1_024


class ExternalRateError(Exception):
    """Raised when a configured risk-free-rate broker cannot return a usable value."""


class _RateBrokerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rf: float


def _build_client() -> httpx.Client:
    timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def fetch_external_risk_free_rate(settings, *, client=None) -> float | None:
    """Return today's risk-free rate as a decimal, or ``None`` if not configured.

    Reuses ``kite_token_broker_passcode`` as the ``x-token-passcode`` header. The
    broker returns a percent; this converts to a decimal in ``[0, 1]``. Raises
    ``ExternalRateError`` on any transport/response problem so callers can fall
    back to the env value.
    """
    broker_url = getattr(settings, "kite_rate_broker_url", None)
    broker_passcode = getattr(settings, "kite_token_broker_passcode", None)
    if broker_url is None:
        return None
    if broker_passcode is None:
        raise ExternalRateError("risk-free rate service passcode is not configured")

    owns_client = client is None
    http = client or _build_client()
    try:
        with http.stream(
            "GET",
            str(broker_url),
            headers={
                "x-token-passcode": broker_passcode.get_secret_value(),
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        ) as response:
            if response.status_code != 200:
                raise ExternalRateError("risk-free rate service request failed")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > MAX_RATE_RESPONSE_BYTES:
                raise ExternalRateError("risk-free rate service response is invalid")
            content_encoding = response.headers.get("content-encoding")
            if content_encoding and content_encoding.lower() != "identity":
                raise ExternalRateError("risk-free rate service response is invalid")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > MAX_RATE_RESPONSE_BYTES:
                    raise ExternalRateError("risk-free rate service response is invalid")

        payload = _RateBrokerResponse.model_validate_json(content)
        # The broker reports a percent (e.g. 5.3324); Greeks want a decimal.
        rate = payload.rf / 100.0
        if rate < 0 or rate > 1:
            raise ExternalRateError("risk-free rate service returned an out-of-range value")
        return rate
    except ExternalRateError:
        raise
    except (httpx.HTTPError, ValidationError, ValueError, TypeError) as exc:
        raise ExternalRateError("risk-free rate service is unavailable or invalid") from exc
    finally:
        if owns_client:
            http.close()


def resolve_daily_risk_free_rate(settings, *, fetcher=None) -> float | None:
    """Fetch the broker rate, falling back to the env ``RISK_FREE_RATE``.

    Returns a decimal in ``[0, 1]`` or ``None`` when neither source is available.
    Broker failures are logged (without secrets) and fall through to the env value.
    """
    fetch = fetcher or (lambda: fetch_external_risk_free_rate(settings))
    try:
        rate = fetch()
    except ExternalRateError as exc:
        logger.warning("risk-free rate broker unavailable (%s); using env fallback", exc)
        rate = None
    if rate is not None:
        return rate
    env_rate = getattr(settings, "risk_free_rate", None)
    return float(env_rate) if env_rate is not None else None
