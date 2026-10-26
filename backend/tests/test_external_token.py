"""Tests for the broker login service token API client."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.kite.external_token import (
    USER_AGENT,
    ExternalTokenError,
    fetch_external_access_token,
    token_broker_configured,
)
from app.kite.totp import code_for_step, decode_seed, step_at

URL = "https://broker.beonedge.in/api/v1/token"
SEED = base64.b32encode(b"0123456789abcdefghij").decode()
NOW = 1_800_000_010.0
TOKEN_BODY = b'{"broker":"kite","client_id":"AB1234","access_token":"ACCESS","expires_in":3600}'


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
    def __init__(self, *responses: FakeResponse | Exception):
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], dict]] = []

    def stream(self, method: str, url: str, *, headers: dict[str, str], content: bytes):
        assert method == "POST"
        self.requests.append((url, headers, json.loads(content)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self, now: float):
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _settings(**overrides):
    values = {
        "kite_user_id": "ab1234",
        "kite_token_broker_url": URL,
        "kite_token_broker_cf_client_id": SecretStr("client-id.access"),
        "kite_token_broker_cf_client_secret": SecretStr("client-secret"),
        "kite_token_broker_totp_seed": SecretStr(SEED),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fetch(client, clock=None, **overrides):
    clock = clock or FakeClock(NOW)
    return fetch_external_access_token(
        _settings(**overrides), client=client, clock=clock, sleep=clock.sleep
    )


def _code_at(unix_seconds: float) -> str:
    return code_for_step(decode_seed(SEED), step_at(unix_seconds))


def test_token_is_fetched_with_the_service_token_and_a_current_code():
    client = FakeClient(FakeResponse(200, TOKEN_BODY))

    assert _fetch(client) == "ACCESS"
    [(url, headers, body)] = client.requests
    assert url == URL
    assert headers["CF-Access-Client-Id"] == "client-id.access"
    assert headers["CF-Access-Client-Secret"] == "client-secret"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept-Encoding"] == "identity"
    assert headers["User-Agent"] == USER_AGENT
    assert body == {"client_id": "AB1234", "totp": _code_at(NOW)}


def test_no_token_yet_returns_none_so_the_scheduler_polls_again():
    client = FakeClient(FakeResponse(503, b'{"error":"no_token_yet"}'))

    assert _fetch(client) is None


def test_a_spent_code_waits_for_the_next_step_and_sends_a_fresh_one():
    clock = FakeClock(NOW)
    client = FakeClient(
        FakeResponse(409, b'{"error":"code_already_used"}', {"retry-after": "20"}),
        FakeResponse(200, TOKEN_BODY),
    )

    assert _fetch(client, clock) == "ACCESS"
    first, second = (body["totp"] for _, _, body in client.requests)
    assert first == _code_at(NOW)
    assert second == _code_at(clock.now)
    assert step_at(clock.now) == step_at(NOW) + 1
    assert clock.slept == [pytest.approx(21.0)]


def test_a_code_spent_on_every_try_gives_up():
    spent = FakeResponse(409, b'{"error":"code_already_used"}')
    client = FakeClient(spent, spent, spent)

    with pytest.raises(ExternalTokenError, match="already used"):
        _fetch(client)
    assert len(client.requests) == 3


@pytest.mark.parametrize(
    "body",
    [
        b'{"broker":"dhan","client_id":"AB1234","access_token":"ACCESS"}',
        b'{"broker":"kite","client_id":"ZZ9999","access_token":"ACCESS"}',
    ],
)
def test_a_token_for_another_account_is_refused(body):
    with pytest.raises(ExternalTokenError, match="another account"):
        _fetch(FakeClient(FakeResponse(200, body)))


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (FakeResponse(401, b'{"error":"unauthorized"}'), "HTTP 401, unauthorized"),
        (FakeResponse(429, b'{"error":"locked"}'), "HTTP 429, locked"),
        (FakeResponse(503, b'{"error":"not_configured"}'), "HTTP 503, not_configured"),
        (FakeResponse(403, b"error code: 1010"), "HTTP 403, unknown"),
        (FakeResponse(302, b"", {"location": "https://elsewhere.example"}), "HTTP 302"),
    ],
)
def test_refusals_name_the_status_and_documented_error(response, reason):
    with pytest.raises(ExternalTokenError, match=reason):
        _fetch(FakeClient(response))


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(200, b"not-json"),
        FakeResponse(200, b"{}"),
        FakeResponse(200, b'{"broker":"kite","client_id":"AB1234","access_token":""}'),
        FakeResponse(200, TOKEN_BODY, {"content-encoding": "gzip"}),
        FakeResponse(200, b"x" * 8_193),
        httpx.TimeoutException("timed out"),
    ],
)
def test_invalid_or_unavailable_service_fails_closed_without_leaking(response, caplog):
    secret = "DO_NOT_LOG_SECRET"
    client = FakeClient(response)

    with pytest.raises(ExternalTokenError, match="external token service") as error:
        _fetch(client, kite_token_broker_cf_client_secret=SecretStr(secret))

    combined_output = f"{error.value} {caplog.text}"
    assert secret not in combined_output
    assert SEED not in combined_output


def test_unconfigured_service_makes_no_request():
    names = (
        "kite_token_broker_url",
        "kite_token_broker_cf_client_id",
        "kite_token_broker_cf_client_secret",
        "kite_token_broker_totp_seed",
    )
    settings = {name: None for name in names}

    assert _fetch(FakeClient(), **settings) is None
    assert not token_broker_configured(_settings(**settings))
    assert token_broker_configured(_settings())


@pytest.mark.parametrize(
    "overrides",
    [
        {"kite_token_broker_totp_seed": None},
        {"kite_user_id": None},
        {"kite_token_broker_totp_seed": SecretStr("not base32!")},
    ],
)
def test_incomplete_or_invalid_configuration_makes_no_request(overrides):
    client = FakeClient()

    with pytest.raises(ExternalTokenError):
        _fetch(client, **overrides)
    assert client.requests == []
