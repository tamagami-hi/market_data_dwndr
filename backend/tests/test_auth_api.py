"""Tests for the SessionService + /api/auth routes (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import create_auth_router
from app.session import SessionState, save_session
from app.session_service import SessionService


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
        timezone="Asia/Kolkata",
        market_open="09:15",
        market_close="15:30",
        state_dir=tmp_path,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(tmp_path, login_fn=None, **overrides):
    def default_login(settings, *, trading_date, risk_free_rate, totp_provider):
        code = totp_provider()  # exercise the provider
        state = SessionState(trading_date, f"ACCESS_{code}", float(risk_free_rate or 0.07), 1, 1)
        save_session(settings.state_dir, state)
        return state

    return SessionService(
        _settings(tmp_path, **overrides),
        login_fn=login_fn or default_login,
        clock=lambda: 1_753_070_400_000,
    )


def _app(service) -> TestClient:
    app = FastAPI()
    app.state.session_service = service
    app.include_router(create_auth_router())
    return TestClient(app)


# --- SessionService ----------------------------------------------------------


def test_status_reports_unauthenticated_then_authenticated(tmp_path):
    service = _service(tmp_path)
    st = service.status()
    assert st["configured"] is True
    assert st["authenticated"] is False
    assert st["credentials_present"] is True
    assert st["access_token"] is None

    service.login(totp="123456", risk_free_rate=0.0691)
    st2 = service.status()
    assert st2["authenticated"] is True
    assert st2["risk_free_rate"] == 0.0691
    assert st2["access_token"].startswith("ACCE") and "\u2026" in st2["access_token"]


def test_login_uses_supplied_totp(tmp_path):
    captured = {}

    def login_fn(settings, *, trading_date, risk_free_rate, totp_provider):
        captured["totp"] = totp_provider()
        state = SessionState(trading_date, "ACCESS", risk_free_rate, 1, 1)
        save_session(settings.state_dir, state)
        return state

    service = _service(tmp_path, login_fn=login_fn)
    service.login(totp="654321", risk_free_rate=0.07)
    assert captured["totp"] == "654321"


# --- routes ------------------------------------------------------------------


def test_status_route(tmp_path):
    client = _app(_service(tmp_path))
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_login_route_then_status(tmp_path):
    client = _app(_service(tmp_path))
    r = client.post("/api/auth/login", json={"totp": "111111", "risk_free_rate": 0.0691})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["trading_date"] == "2025-07-21"  # fixed clock (1_753_070_400_000 ms, IST)
    assert body["risk_free_rate"] == 0.0691
    # status now reflects the session
    assert client.get("/api/auth/status").json()["authenticated"] is True


def test_login_route_surfaces_login_error(tmp_path):
    from app.kite.login import KiteLoginError

    def failing(settings, *, trading_date, risk_free_rate, totp_provider):
        raise KiteLoginError("twofa failed: invalid code")

    client = _app(_service(tmp_path, login_fn=failing))
    r = client.post("/api/auth/login", json={"totp": "000000", "risk_free_rate": 0.07})
    assert r.status_code == 400
    assert "twofa failed" in r.json()["detail"]


def test_login_route_requires_credentials_or_request_token(tmp_path):
    client = _app(_service(tmp_path, kite_user_id=None, kite_password=None))
    r = client.post("/api/auth/login", json={"totp": "111111", "risk_free_rate": 0.07})
    assert r.status_code == 400
    assert "request_token" in r.json()["detail"]


def test_login_url_route(tmp_path):
    client = _app(_service(tmp_path))
    r = client.get("/api/auth/login-url")
    assert r.status_code == 200
    assert "kite.zerodha.com/connect/login" in r.json()["login_url"]
    assert "api_key=apikey" in r.json()["login_url"]


def test_routes_degrade_when_unconfigured():
    app = FastAPI()
    app.state.session_service = None
    app.include_router(create_auth_router())
    client = TestClient(app)
    assert client.get("/api/auth/status").json() == {"configured": False, "authenticated": False}
    assert client.get("/api/auth/login-url").status_code == 503
    assert client.post("/api/auth/login", json={"totp": "1"}).status_code == 503
