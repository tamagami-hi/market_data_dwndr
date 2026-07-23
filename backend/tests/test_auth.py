"""Tests for session-state persistence and the Kite login flow (mocked)."""

from __future__ import annotations

import hashlib
import stat

import pytest

from app.kite.auth import (
    KiteAuthenticator,
    auth_header,
    compute_checksum,
    login_url,
)
from app.session import SessionState, load_session, save_session


def test_login_url_contains_key_and_version():
    url = login_url("abcd1234")
    assert "api_key=abcd1234" in url
    assert "v=3" in url


def test_compute_checksum_matches_sha256():
    expected = hashlib.sha256(b"apikeyreqtoksecret").hexdigest()
    assert compute_checksum("apikey", "reqtok", "secret") == expected


def test_auth_header_shape():
    hdr = auth_header("apikey", "tok")
    assert hdr["X-Kite-Version"] == "3"
    assert hdr["Authorization"] == "token apikey:tok"


def test_session_state_round_trip(tmp_path):
    state = SessionState(
        trading_date="2026-07-21",
        access_token="tok123",
        risk_free_rate=0.0691,
        access_token_at=1_753_070_400_000,
        started_at=1_753_070_400_000,
    )
    path = save_session(tmp_path, state)
    assert path.name == "session-2026-07-21.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_session(tmp_path, "2026-07-21")
    assert loaded == state
    assert load_session(tmp_path, "2026-07-22") is None


def test_authenticate_persists_token_and_risk_free_rate(tmp_path):
    calls = {}

    def fake_generate(request_token: str, api_secret: str) -> dict:
        calls["request_token"] = request_token
        calls["api_secret"] = api_secret
        return {"access_token": "ACCESS_TOKEN_XYZ", "user_id": "AB1234"}

    auth = KiteAuthenticator(
        api_key="apikey",
        api_secret="secret",
        state_dir=tmp_path,
        session_generator=fake_generate,
        clock=lambda: 1_753_070_400_000,
    )
    state = auth.authenticate(
        request_token="reqtok", risk_free_rate=0.0691, trading_date="2026-07-21"
    )
    # DoD: login yields a usable access_token; risk-free rate stored in session state.
    assert state.access_token == "ACCESS_TOKEN_XYZ"
    assert state.risk_free_rate == 0.0691
    assert calls == {"request_token": "reqtok", "api_secret": "secret"}
    # Persisted and reloadable.
    assert load_session(tmp_path, "2026-07-21").access_token == "ACCESS_TOKEN_XYZ"


def test_get_or_login_reuses_existing_session(tmp_path):
    def fail_generate(request_token: str, api_secret: str) -> dict:  # pragma: no cover
        raise AssertionError("should not re-login when a session exists")

    auth = KiteAuthenticator(
        api_key="apikey",
        api_secret="secret",
        state_dir=tmp_path,
        session_generator=fail_generate,
        clock=lambda: 111,
    )
    save_session(
        tmp_path,
        SessionState("2026-07-21", "EXISTING", 0.07, 100, 100),
    )
    state = auth.get_or_login("2026-07-21")
    assert state.access_token == "EXISTING"


def test_get_or_login_requires_inputs_when_no_session(tmp_path):
    auth = KiteAuthenticator("apikey", "secret", tmp_path, session_generator=lambda r, s: {})
    with pytest.raises(RuntimeError):
        auth.get_or_login("2026-07-21")
