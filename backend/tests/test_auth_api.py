"""Tests for the SessionService + /api/auth routes (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import create_auth_router
from app.kite.external_token import ExternalTokenError
from app.kite.login_flow import LoginCoordinator, LoginMethod, LoginProgress, LoginStep
from app.session import SessionState, save_session
from app.session_service import SessionService


def _settings(tmp_path, **overrides):
    base = dict(
        kite_api_key="apikey",
        kite_api_secret="secret",
        kite_user_id="AB1234",
        kite_password="pass",
        kite_static_ip=None,
        kite_http_proxy=None,
        risk_free_rate=None,
        timezone="Asia/Kolkata",
        market_open="09:15",
        market_close="15:30",
        cors_origins=["http://localhost:3000"],
        state_dir=tmp_path,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(tmp_path, login_fn=None, login_flow=None, **overrides):
    def default_login(settings, *, trading_date, risk_free_rate, totp_provider):
        code = totp_provider()  # exercise the provider
        state = SessionState(trading_date, f"ACCESS_{code}", float(risk_free_rate or 0.07), 1, 1)
        save_session(settings.state_dir, state)
        return state

    return SessionService(
        _settings(tmp_path, **overrides),
        login_fn=login_fn or default_login,
        login_flow=login_flow,
        clock=lambda: 1_753_070_400_000,
    )


def _app(service) -> TestClient:
    app = FastAPI()
    app.state.session_service = service
    app.include_router(create_auth_router())
    return TestClient(app, headers={"Origin": "http://localhost:3000"})


# --- SessionService ----------------------------------------------------------


def test_status_reports_unauthenticated_then_authenticated(tmp_path):
    service = _service(tmp_path)
    st = service.status()
    assert st["configured"] is True
    assert st["authenticated"] is False
    assert st["credentials_present"] is True
    assert "access_token" not in st
    assert "has_totp_secret" not in st

    service.login(totp="123456", risk_free_rate=0.0691)
    st2 = service.status()
    assert st2["authenticated"] is True
    assert st2["risk_free_rate"] == 0.0691
    assert "access_token" not in st2


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


def test_automated_service_login_requires_user_entered_totp(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="TOTP must be entered"):
        service.login(risk_free_rate=0.07)


# --- routes ------------------------------------------------------------------


def test_status_route(tmp_path):
    client = _app(_service(tmp_path))
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_status_route_includes_redacted_daily_automation_state(tmp_path):
    app = FastAPI()
    app.state.session_service = _service(tmp_path)
    app.state.daily_automation = SimpleNamespace(
        status=lambda: {
            "phase": "auth_window",
            "last_error": "shared token is not ready; retrying in the auth window",
        }
    )
    app.include_router(create_auth_router())
    client = TestClient(app, headers={"Origin": "http://localhost:3000"})

    response = client.get("/api/auth/status")

    assert response.json()["automation"]["phase"] == "auth_window"
    assert "TOKEN_MUST_NOT_ESCAPE" not in response.text


def test_risk_free_rate_update_route_clears_third_day_requirement(tmp_path):
    service = _service(tmp_path)
    trading_date = service.trading_date()
    save_session(
        tmp_path,
        SessionState(
            trading_date,
            "ACCESS",
            0.065,
            1,
            1,
            risk_free_rate_as_of="2025-07-19",
            rate_update_required=True,
        ),
    )
    client = _app(service)

    response = client.put("/api/auth/risk-free-rate", json={"risk_free_rate": 0.066})

    assert response.status_code == 200
    assert response.json()["risk_free_rate"] == 0.066
    status = client.get("/api/auth/status").json()
    assert status["rate_update_required"] is False
    assert status["capture_ready"] is True


def test_legacy_login_rejects_automated_body(tmp_path):
    client = _app(_service(tmp_path))
    r = client.post("/api/auth/login", json={"totp": "111111", "risk_free_rate": 0.0691})
    assert r.status_code == 422


class FakeLoginFlow:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        self.calls = []

    def start(self, trading_date):
        self.calls.append(("start", trading_date))
        return LoginProgress(
            "opaque-attempt",
            LoginStep.AWAITING_TOTP,
            LoginMethod.LOCAL_CREDENTIALS,
            trading_date,
            2_000,
        )

    def submit_totp(self, attempt_id, totp):
        self.calls.append(("totp", attempt_id, totp))
        return LoginProgress(
            attempt_id,
            LoginStep.AWAITING_RISK_FREE_RATE,
            LoginMethod.LOCAL_CREDENTIALS,
            "2025-07-21",
            2_000,
        )

    def complete(self, attempt_id, risk_free_rate):
        self.calls.append(("complete", attempt_id, risk_free_rate))
        state = SessionState("2025-07-21", "ACCESS", risk_free_rate, 1, 1)
        save_session(self.state_dir, state)
        return state

    def cancel(self, attempt_id):
        self.calls.append(("cancel", attempt_id))

    def close(self):
        self.calls.append(("close",))


def test_staged_login_api_cycle(tmp_path):
    flow = FakeLoginFlow(tmp_path)
    client = _app(_service(tmp_path, login_flow=flow))

    started = client.post("/api/auth/login/start")
    assert started.status_code == 202
    assert started.json() == {
        "attempt_id": "opaque-attempt",
        "step": "awaiting_totp",
        "method": "local_credentials",
        "trading_date": "2025-07-21",
        "expires_at": 2000,
    }

    totp = client.post("/api/auth/login/opaque-attempt/totp", json={"totp": "654321"})
    assert totp.status_code == 200
    assert totp.json()["step"] == "awaiting_risk_free_rate"

    completed = client.post(
        "/api/auth/login/opaque-attempt/complete", json={"risk_free_rate": 0.0691}
    )
    assert completed.status_code == 200
    assert completed.json()["authenticated"] is True
    assert client.get("/api/auth/status").json()["authenticated"] is True
    assert flow.calls == [
        ("start", "2025-07-21"),
        ("totp", "opaque-attempt", "654321"),
        ("complete", "opaque-attempt", 0.0691),
    ]


def test_external_token_api_cycle_skips_totp_and_never_returns_token(tmp_path):
    settings = _settings(tmp_path)
    flow = LoginCoordinator(
        settings,
        client_factory=lambda *_: (_ for _ in ()).throw(
            AssertionError("local login must be skipped")
        ),
        external_token_fetcher=lambda: "VPS_SECRET_ACCESS_TOKEN",
        external_token_validator=lambda _: None,
        clock=lambda: 1_753_070_400_000,
    )
    client = _app(_service(tmp_path, login_flow=flow))

    started = client.post("/api/auth/login/start")

    assert started.status_code == 202
    assert started.json()["step"] == "awaiting_risk_free_rate"
    assert started.json()["method"] == "shared_session"
    assert "VPS_SECRET_ACCESS_TOKEN" not in started.text

    completed = client.post(
        f"/api/auth/login/{started.json()['attempt_id']}/complete",
        json={"risk_free_rate": 0.0691},
    )
    assert completed.status_code == 200
    assert "VPS_SECRET_ACCESS_TOKEN" not in completed.text
    assert "VPS_SECRET_ACCESS_TOKEN" not in client.get("/api/auth/status").text


def test_external_token_failure_is_sanitized(tmp_path, caplog):
    secret = "MUST_NOT_ESCAPE"
    flow = FakeLoginFlow(tmp_path)

    def fail_start(trading_date):
        raise ExternalTokenError(f"external token service failed: {secret}")

    flow.start = fail_start
    client = _app(_service(tmp_path, login_flow=flow))

    response = client.post("/api/auth/login/start")

    assert response.status_code == 502
    assert response.json()["detail"] == "shared token service is unavailable; retry later"
    assert secret not in response.text
    assert secret not in caplog.text


@pytest.mark.parametrize("totp", ["", "12345", "12ab56", "१२३४५६"])
def test_staged_login_api_rejects_invalid_totp(tmp_path, totp):
    flow = FakeLoginFlow(tmp_path)
    client = _app(_service(tmp_path, login_flow=flow))

    response = client.post("/api/auth/login/opaque-attempt/totp", json={"totp": totp})

    assert response.status_code == 422
    assert flow.calls == []


def test_one_shot_api_never_prompts_terminal_for_missing_totp(tmp_path):
    client = _app(_service(tmp_path))

    response = client.post("/api/auth/login", json={"risk_free_rate": 0.07})

    assert response.status_code == 422


def test_login_route_requires_credentials_or_request_token(tmp_path):
    client = _app(_service(tmp_path, kite_user_id=None, kite_password=None))
    r = client.post("/api/auth/login", json={"totp": "111111", "risk_free_rate": 0.07})
    assert r.status_code == 422


def test_staged_login_rejects_untrusted_origin(tmp_path):
    client = _app(_service(tmp_path))

    response = client.post("/api/auth/login/start", headers={"Origin": "http://malicious.example"})

    assert response.status_code == 403


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
    assert (
        client.post(
            "/api/auth/login",
            json={"request_token": "token", "risk_free_rate": 0.07},
        ).status_code
        == 503
    )
