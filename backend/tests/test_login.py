"""Tests for the automated Kite login (mocked HTTP client, no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pyotp
import pytest

from app.kite import login
from app.kite.login import (
    KiteLoginError,
    _extract_request_token,
    _require_success,
    build_kite_http_client,
    exchange_request_token,
    fetch_request_token,
    make_totp_provider,
    run_login,
    totp_from_secret,
)
from app.session import load_session

# --- fakes -------------------------------------------------------------------


class FakeResp:
    def __init__(self, status_code=200, json_body=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeClient:
    """Routes POSTs by URL substring; serves GETs from a queue (redirect chain)."""

    def __init__(self, post_routes=None, get_queue=None):
        self.post_routes = post_routes or {}
        self.get_queue = list(get_queue or [])
        self.posts: list[tuple] = []
        self.gets: list[str] = []
        self.closed = False

    def post(self, url, data=None, headers=None):
        self.posts.append((url, data, headers))
        for key, resp in self.post_routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, headers=None):
        self.gets.append(url)
        return self.get_queue.pop(0)

    def close(self):
        self.closed = True


# --- TOTP --------------------------------------------------------------------


def test_totp_from_secret_matches_pyotp():
    secret = pyotp.random_base32()
    code = totp_from_secret(secret)
    assert len(code) == 6 and code.isdigit()
    assert code == pyotp.TOTP(secret).now()


def test_make_totp_provider_uses_secret_or_prompt():
    secret = pyotp.random_base32()
    provider = make_totp_provider(secret)
    assert provider() == pyotp.TOTP(secret).now()
    assert make_totp_provider(None) is login.prompt_totp


# --- helpers -----------------------------------------------------------------


def test_extract_request_token():
    url = "https://app.example/cb?request_token=TOK123&action=login&status=success"
    assert _extract_request_token(url) == "TOK123"
    assert _extract_request_token("https://app.example/cb?status=success") is None
    assert _extract_request_token(None) is None


def test_require_success_raises_on_error():
    ok = _require_success(FakeResp(json_body={"status": "success", "data": {"x": 1}}), "s")
    assert ok == {"x": 1}
    with pytest.raises(KiteLoginError, match="bad creds"):
        _require_success(
            FakeResp(json_body={"status": "error", "message": "bad creds"}), "login"
        )


# --- request_token flow ------------------------------------------------------


def _login_client(final_location):
    return FakeClient(
        post_routes={
            "/api/login": FakeResp(
                json_body={
                    "status": "success",
                    "data": {"request_id": "req123", "twofa_type": "totp"},
                }
            ),
            "/api/twofa": FakeResp(json_body={"status": "success", "data": {}}),
        },
        get_queue=[
            # connect/login -> internal kite redirect (no token) -> follow
            FakeResp(302, headers={"location": "https://kite.zerodha.com/connect/finish?sess=1"}),
            # connect/finish -> external redirect carrying the request_token
            FakeResp(302, headers={"location": final_location}),
        ],
    )


def test_fetch_request_token_walks_redirects():
    client = _login_client("https://myapp.example/cb?request_token=RT_OK&status=success")
    seen = {}

    def totp():
        seen["called"] = True
        return "654321"

    token = fetch_request_token(client, "apikey", "AB1234", "pass", totp)
    assert token == "RT_OK"
    assert seen.get("called") is True
    # twofa was posted with the generated code
    twofa_post = next(p for p in client.posts if "/api/twofa" in p[0])
    assert twofa_post[1]["twofa_value"] == "654321"
    assert twofa_post[1]["request_id"] == "req123"


def test_fetch_request_token_offsite_without_token_errors():
    client = _login_client("https://evil.example/cb?status=success")  # no request_token
    with pytest.raises(KiteLoginError, match="off-site"):
        fetch_request_token(client, "apikey", "AB1234", "pass", lambda: "111111")


def test_fetch_request_token_login_failure():
    client = FakeClient(
        post_routes={"/api/login": FakeResp(json_body={"status": "error", "message": "nope"})}
    )
    with pytest.raises(KiteLoginError, match="login failed"):
        fetch_request_token(client, "apikey", "AB1234", "pass", lambda: "111111")


# --- token exchange ----------------------------------------------------------


def test_exchange_request_token():
    client = FakeClient(
        post_routes={
            "/session/token": FakeResp(
                json_body={"status": "success", "data": {"access_token": "ACCESS_XYZ"}}
            )
        }
    )
    token = exchange_request_token(client, "apikey", "secret", "reqtok")
    assert token == "ACCESS_XYZ"
    url, data, headers = client.posts[0]
    assert "/session/token" in url
    assert data["api_key"] == "apikey" and data["request_token"] == "reqtok"
    assert len(data["checksum"]) == 64  # sha256 hex
    assert headers["X-Kite-Version"] == "3"


# --- end-to-end run_login ----------------------------------------------------


def _settings(tmp_path, **overrides):
    base = dict(
        kite_api_key="apikey",
        kite_api_secret="secret",
        kite_user_id="AB1234",
        kite_password="pass",
        kite_totp_secret=None,
        kite_static_ip=None,
        kite_http_proxy=None,
        risk_free_rate=None,
        state_dir=tmp_path,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_run_login_persists_session(tmp_path):
    client = _login_client("https://myapp.example/cb?request_token=RT&status=success")
    client.post_routes["/session/token"] = FakeResp(
        json_body={"status": "success", "data": {"access_token": "ACCESS_1"}}
    )
    settings = _settings(tmp_path)

    state = run_login(
        settings,
        trading_date="2026-07-21",
        risk_free_rate=0.0691,
        totp_provider=lambda: "222222",
        client=client,
    )
    assert state.access_token == "ACCESS_1"
    assert state.risk_free_rate == 0.0691
    assert not client.closed  # caller-owned client not closed
    # persisted to _state/
    reloaded = load_session(tmp_path, "2026-07-21")
    assert reloaded.access_token == "ACCESS_1"


def test_run_login_requires_credentials(tmp_path):
    with pytest.raises(KiteLoginError, match="KITE_USER_ID"):
        run_login(
            _settings(tmp_path, kite_user_id=None),
            trading_date="2026-07-21",
            risk_free_rate=0.07,
        )


def test_run_login_requires_rate(tmp_path):
    with pytest.raises(KiteLoginError, match="risk_free_rate"):
        run_login(
            _settings(tmp_path),
            trading_date="2026-07-21",
            totp_provider=lambda: "1",
            client=FakeClient(),
        )


# --- client builder ----------------------------------------------------------


def test_build_kite_http_client_variants():
    plain = build_kite_http_client()
    plain.close()
    bound = build_kite_http_client(static_ip="127.0.0.1")
    bound.close()
    proxied = build_kite_http_client(proxy="http://127.0.0.1:9")
    proxied.close()
