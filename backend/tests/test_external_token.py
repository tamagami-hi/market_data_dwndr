"""Tests for the backend-only VPS Kite token broker client."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.kite.external_token import ExternalTokenError, fetch_external_access_token


class FakeResponse:
    def __init__(self, status_code: int, content: bytes, headers: dict | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_bytes(self):
        midpoint = min(len(self.content), 4_096)
        yield self.content[:midpoint]
        yield self.content[midpoint:]


class FakeClient:
    def __init__(self, response: FakeResponse | Exception):
        self.response = response
        self.requests: list[tuple[str, dict[str, str]]] = []

    def stream(self, method: str, url: str, *, headers: dict[str, str]):
        assert method == "GET"
        self.requests.append((url, headers))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def get(self, url: str, *, headers: dict[str, str]):
        raise AssertionError("broker responses must be streamed")


def _settings(**overrides):
    values = {
        "kite_token_broker_url": "https://calspread.online/api/kite/token",
        "kite_token_broker_passcode": SecretStr("test-passcode"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_authenticated_response_returns_token_and_uses_secret_header():
    client = FakeClient(FakeResponse(200, b'{"authenticated":true,"access_token":"ACCESS"}'))

    token = fetch_external_access_token(_settings(), client=client)

    assert token == "ACCESS"
    assert client.requests == [
        (
            "https://calspread.online/api/kite/token",
            {
                "x-token-passcode": "test-passcode",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
    ]


def test_explicit_unauthenticated_response_allows_local_login_fallback():
    client = FakeClient(
        FakeResponse(
            409,
            b'{"authenticated":false,"error":"No active Zerodha session."}',
        )
    )

    assert fetch_external_access_token(_settings(), client=client) is None


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(401, b'{"error":"bad passcode"}'),
        FakeResponse(302, b"", {"location": "https://elsewhere.example"}),
        FakeResponse(200, b"not-json"),
        FakeResponse(200, b"{}"),
        FakeResponse(200, b'{"authenticated":true}'),
        FakeResponse(200, b'{"authenticated":true,"access_token":""}'),
        FakeResponse(
            200,
            b'{"authenticated":true,"access_token":"ACCESS"}',
            {"content-encoding": "gzip"},
        ),
        FakeResponse(200, b"x" * 8_193),
        httpx.TimeoutException("timed out"),
    ],
)
def test_invalid_or_unavailable_broker_response_fails_closed_without_leaking(response, caplog):
    secret = "DO_NOT_LOG_PASSCODE"
    token_fragment = "DO_NOT_LOG_TOKEN"
    client = FakeClient(response)

    with pytest.raises(ExternalTokenError, match="external token service") as error:
        fetch_external_access_token(
            _settings(kite_token_broker_passcode=SecretStr(secret)),
            client=client,
        )

    combined_output = f"{error.value} {caplog.text}"
    assert secret not in combined_output
    assert token_fragment not in combined_output


def test_unconfigured_broker_does_not_make_a_request():
    client = FakeClient(AssertionError("must not call broker"))

    result = fetch_external_access_token(
        _settings(kite_token_broker_url=None, kite_token_broker_passcode=None),
        client=client,
    )

    assert result is None
