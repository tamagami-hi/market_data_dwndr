"""Tests for the backend-only calspread risk-free-rate broker client."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.kite.external_rate import (
    ExternalRateError,
    fetch_external_risk_free_rate,
    resolve_daily_risk_free_rate,
)


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
        midpoint = min(len(self.content), 2_048)
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


def _settings(**overrides):
    values = {
        "kite_rate_broker_url": "https://calspread.online/api/rf",
        "kite_token_broker_passcode": SecretStr("test-passcode"),
        "risk_free_rate": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_percent_response_is_converted_to_decimal_and_reuses_passcode():
    client = FakeClient(FakeResponse(200, b'{"rf":5.3324}'))

    rate = fetch_external_risk_free_rate(_settings(), client=client)

    assert rate == pytest.approx(0.053324)
    assert client.requests == [
        (
            "https://calspread.online/api/rf",
            {
                "x-token-passcode": "test-passcode",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )
    ]


def test_unconfigured_rate_broker_returns_none_without_request():
    client = FakeClient(AssertionError("must not call broker"))

    assert (
        fetch_external_risk_free_rate(_settings(kite_rate_broker_url=None), client=client)
        is None
    )


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(401, b'{"error":"bad passcode"}'),
        FakeResponse(302, b"", {"location": "https://elsewhere.example"}),
        FakeResponse(200, b"not-json"),
        FakeResponse(200, b"{}"),
        FakeResponse(200, b'{"rf":150.0}'),  # out of range after /100
        FakeResponse(200, b'{"rf":5.0}', {"content-encoding": "gzip"}),
        FakeResponse(200, b"x" * 4_097),
        httpx.TimeoutException("timed out"),
    ],
)
def test_invalid_or_unavailable_rate_broker_fails_closed_without_leaking(response, caplog):
    secret = "DO_NOT_LOG_PASSCODE"
    client = FakeClient(response)

    with pytest.raises(ExternalRateError, match="risk-free rate service") as error:
        fetch_external_risk_free_rate(
            _settings(kite_token_broker_passcode=SecretStr(secret)),
            client=client,
        )

    assert secret not in f"{error.value} {caplog.text}"


def test_resolver_prefers_broker_value():
    rate = resolve_daily_risk_free_rate(_settings(risk_free_rate=0.07), fetcher=lambda: 0.0533)
    assert rate == 0.0533


def test_resolver_falls_back_to_env_when_broker_unavailable():
    def failing():
        raise ExternalRateError("risk-free rate service is unavailable or invalid")

    rate = resolve_daily_risk_free_rate(_settings(risk_free_rate=0.068), fetcher=failing)
    assert rate == 0.068


def test_resolver_returns_none_when_neither_source_available():
    rate = resolve_daily_risk_free_rate(_settings(risk_free_rate=None), fetcher=lambda: None)
    assert rate is None
