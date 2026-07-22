"""Tests for the staged automated-login coordinator (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.kite.external_token import ExternalTokenError
from app.kite.login import KiteLoginError
from app.kite.login_flow import LoginCoordinator, LoginMethod, LoginStep
from app.session import load_session


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._json_body


class FakeClient:
    def __init__(self):
        self.posts = []
        self.closed = False
        self._redirects = [
            FakeResponse(302, headers={"location": "https://kite.zerodha.com/finish"}),
            FakeResponse(302, headers={"location": "https://app/cb?request_token=REQ"}),
        ]

    def post(self, url, data=None, headers=None):
        self.posts.append((url, data, headers))
        if "/api/login" in url:
            return FakeResponse(
                json_body={
                    "status": "success",
                    "data": {"request_id": "kite-internal", "twofa_type": "totp"},
                }
            )
        if "/api/twofa" in url:
            return FakeResponse(json_body={"status": "success", "data": {}})
        if "/session/token" in url:
            return FakeResponse(
                json_body={"status": "success", "data": {"access_token": "ACCESS_TOKEN"}}
            )
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, headers=None):
        return self._redirects.pop(0)

    def close(self):
        self.closed = True


def _settings(tmp_path):
    return SimpleNamespace(
        kite_api_key="apikey",
        kite_api_secret="secret",
        kite_user_id="AB1234",
        kite_password="password",
        kite_static_ip=None,
        kite_http_proxy=None,
        state_dir=tmp_path,
    )


def test_staged_login_uses_env_credentials_then_totp_then_rate(tmp_path):
    client = FakeClient()
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        client_factory=lambda *_: client,
        clock=lambda: 1_000,
    )

    started = coordinator.start("2026-07-22")
    assert started.step is LoginStep.AWAITING_TOTP
    assert started.method is LoginMethod.LOCAL_CREDENTIALS
    assert started.attempt_id != "kite-internal"
    assert client.posts[0][1] == {"user_id": "AB1234", "password": "password"}

    challenged = coordinator.submit_totp(started.attempt_id, "654321")
    assert challenged.step is LoginStep.AWAITING_RISK_FREE_RATE
    assert (
        next(post for post in client.posts if "/api/twofa" in post[0])[1]["twofa_value"] == "654321"
    )

    state = coordinator.complete(started.attempt_id, 0.0691)
    assert state.access_token == "ACCESS_TOKEN"
    assert state.risk_free_rate == 0.0691
    assert load_session(tmp_path, "2026-07-22") == state
    assert client.closed is True


def test_external_access_token_skips_totp_and_waits_for_rate(tmp_path):
    validated_tokens = []
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        client_factory=lambda *_: (_ for _ in ()).throw(
            AssertionError("local credential login must be skipped")
        ),
        external_token_fetcher=lambda: "VPS_ACCESS_TOKEN",
        external_token_validator=validated_tokens.append,
        clock=lambda: 1_000,
    )

    started = coordinator.start("2026-07-22")

    assert started.step is LoginStep.AWAITING_RISK_FREE_RATE
    assert started.method is LoginMethod.SHARED_SESSION
    assert validated_tokens == ["VPS_ACCESS_TOKEN"]
    state = coordinator.complete(started.attempt_id, 0.0691)
    assert state.access_token == "VPS_ACCESS_TOKEN"
    assert state.risk_free_rate == 0.0691
    assert load_session(tmp_path, "2026-07-22") == state


def test_external_no_session_falls_back_to_env_credentials(tmp_path):
    client = FakeClient()
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        client_factory=lambda *_: client,
        external_token_fetcher=lambda: None,
        clock=lambda: 1_000,
    )

    started = coordinator.start("2026-07-22")

    assert started.step is LoginStep.AWAITING_TOTP
    assert client.posts[0][1] == {"user_id": "AB1234", "password": "password"}


def test_invalid_external_access_token_does_not_create_attempt(tmp_path):
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        external_token_fetcher=lambda: "WRONG_APP_TOKEN",
        external_token_validator=lambda _: (_ for _ in ()).throw(KiteLoginError("wrong API key")),
        clock=lambda: 1_000,
    )

    with pytest.raises(ExternalTokenError, match="unusable token"):
        coordinator.start("2026-07-22")
    assert load_session(tmp_path, "2026-07-22") is None


def test_attempt_is_single_use(tmp_path):
    client = FakeClient()
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: client, clock=lambda: 1_000
    )
    attempt = coordinator.start("2026-07-22")
    coordinator.submit_totp(attempt.attempt_id, "654321")
    coordinator.complete(attempt.attempt_id, 0.07)

    with pytest.raises(KiteLoginError, match="not found"):
        coordinator.complete(attempt.attempt_id, 0.07)


@pytest.mark.parametrize("rate", [-0.01, float("inf"), float("nan")])
def test_invalid_rate_does_not_consume_attempt(tmp_path, rate):
    client = FakeClient()
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: client, clock=lambda: 1_000
    )
    attempt = coordinator.start("2026-07-22")
    coordinator.submit_totp(attempt.attempt_id, "654321")

    with pytest.raises(KiteLoginError, match="risk_free_rate"):
        coordinator.complete(attempt.attempt_id, rate)

    assert client.closed is False


def test_close_cancels_pending_attempts(tmp_path):
    client = FakeClient()
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: client, clock=lambda: 1_000
    )
    coordinator.start("2026-07-22")

    coordinator.close()

    assert client.closed is True


def test_cancel_is_idempotent_for_missing_attempt(tmp_path):
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: FakeClient(), clock=lambda: 1_000
    )

    coordinator.cancel("already-gone")


def test_second_start_does_not_disclose_active_attempt_id(tmp_path):
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: FakeClient(), clock=lambda: 1_000
    )
    coordinator.start("2026-07-22")

    with pytest.raises(KiteLoginError, match="already in progress"):
        coordinator.start("2026-07-22")


def test_failed_totp_consumes_attempt(tmp_path):
    client = FakeClient()
    client.post = lambda url, data=None, headers=None: (
        FakeResponse(
            json_body={
                "status": "success",
                "data": {"request_id": "kite-internal", "twofa_type": "totp"},
            }
        )
        if "/api/login" in url
        else FakeResponse(json_body={"status": "error", "message": "invalid TOTP"})
    )
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: client, clock=lambda: 1_000
    )
    attempt = coordinator.start("2026-07-22")

    with pytest.raises(KiteLoginError, match="invalid TOTP"):
        coordinator.submit_totp(attempt.attempt_id, "654321")
    with pytest.raises(KiteLoginError, match="not found"):
        coordinator.submit_totp(attempt.attempt_id, "654321")
    assert client.closed is True


def test_failed_token_exchange_consumes_attempt(tmp_path):
    client = FakeClient()
    original_post = client.post

    def failing_exchange(url, data=None, headers=None):
        if "/session/token" in url:
            return FakeResponse(json_body={"status": "error", "message": "exchange failed"})
        return original_post(url, data=data, headers=headers)

    client.post = failing_exchange
    coordinator = LoginCoordinator(
        _settings(tmp_path), client_factory=lambda *_: client, clock=lambda: 1_000
    )
    attempt = coordinator.start("2026-07-22")
    coordinator.submit_totp(attempt.attempt_id, "654321")

    with pytest.raises(KiteLoginError, match="exchange failed"):
        coordinator.complete(attempt.attempt_id, 0.0691)
    with pytest.raises(KiteLoginError, match="not found"):
        coordinator.complete(attempt.attempt_id, 0.0691)
    assert client.closed is True
