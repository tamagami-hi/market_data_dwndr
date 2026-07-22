"""Backend-only client for retrieving an existing Kite token from the VPS broker."""

from __future__ import annotations

from typing import Annotated

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

MAX_BROKER_RESPONSE_BYTES = 8 * 1_024


class ExternalTokenError(Exception):
    """Raised when a configured token broker cannot return a trustworthy result."""


class _TokenBrokerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authenticated: StrictBool
    access_token: Annotated[str | None, Field(min_length=1, max_length=2_048)] = None
    error: str | None = None


def _build_client() -> httpx.Client:
    timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
    return httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )


def fetch_external_access_token(settings, *, client=None) -> str | None:
    """Return a broker token, or ``None`` only for an explicit unauthenticated result."""
    broker_url = getattr(settings, "kite_token_broker_url", None)
    broker_passcode = getattr(settings, "kite_token_broker_passcode", None)
    if broker_url is None and broker_passcode is None:
        return None
    if broker_url is None or broker_passcode is None:
        raise ExternalTokenError("external token service configuration is incomplete")

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
            if response.status_code not in (200, 409):
                raise ExternalTokenError("external token service request failed")
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
            status_code = response.status_code

        payload = _TokenBrokerResponse.model_validate_json(content)
        if payload.authenticated:
            if status_code != 200:
                raise ExternalTokenError("external token service response is invalid")
            if not payload.access_token or not payload.access_token.strip():
                raise ExternalTokenError("external token service response is invalid")
            return payload.access_token.strip()
        if payload.access_token is not None:
            raise ExternalTokenError("external token service response is invalid")
        return None
    except ExternalTokenError:
        raise
    except (httpx.HTTPError, ValidationError, ValueError, TypeError) as exc:
        raise ExternalTokenError("external token service is unavailable or invalid") from exc
    finally:
        if owns_client:
            http.close()
