"""Security contract for the retained manual login recovery routes.

The recovery flow stays available — it is the operator's way back in when the token broker
is down — but before this it was reachable by any internet caller behind nothing but an
``Origin`` header, which only browsers honour. Two capabilities were exposed: holding the
single login-attempt slot (denying the operator a login) and driving repeated upstream
Zerodha password submissions with the server's stored credentials, which risks a lockout on
the real brokerage account.

Authorization now reuses ``RELEASE_MAINTENANCE_TOKEN`` / ``X-Release-Maintenance-Token`` —
the same secret and header the release scripts already carry — and the stored-credential
fallback is opt-in via ``KITE_PASSWORD_LOGIN_ENABLED`` with a per-window cap.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import OPERATOR_TOKEN_HEADER, create_auth_router
from app.kite.login_flow import (
    PASSWORD_LOGIN_MAX_PER_WINDOW,
    PASSWORD_LOGIN_WINDOW_MS,
    LoginCoordinator,
    PasswordLoginBudgetError,
    PasswordLoginDisabledError,
)
from app.session import SessionState, save_session
from app.session_service import SessionService

OPERATOR_TOKEN = "operator-token-for-tests"

MUTATION_ROUTES = (
    ("post", "/api/auth/login/start", None),
    ("post", "/api/auth/login/abc/totp", {"totp": "123456"}),
    ("post", "/api/auth/login/abc/complete", {"risk_free_rate": 0.07}),
    ("delete", "/api/auth/login/abc", None),
    ("post", "/api/auth/login", {"request_token": "rt", "risk_free_rate": 0.07}),
)


def _settings(tmp_path, **overrides):
    base = dict(
        kite_api_key="apikey",
        kite_api_secret="secret",
        kite_user_id="AB1234",
        kite_password="pass",
        kite_password_login_enabled=False,
        risk_free_rate=None,
        timezone="Asia/Kolkata",
        market_open="09:15",
        market_close="15:30",
        cors_origins=["http://localhost:3000"],
        release_maintenance_token=OPERATOR_TOKEN,
        state_dir=tmp_path,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _service(tmp_path, login_flow=None, **overrides):
    def default_login(settings, *, trading_date, risk_free_rate, totp_provider):
        state = SessionState(trading_date, "ACCESS", float(risk_free_rate or 0.07), 1, 1)
        save_session(settings.state_dir, state)
        return state

    return SessionService(
        _settings(tmp_path, **overrides),
        login_fn=default_login,
        login_flow=login_flow,
        clock=lambda: 1_753_070_400_000,
    )


def _client(service, *, token: str | None = OPERATOR_TOKEN) -> TestClient:
    app = FastAPI()
    app.state.session_service = service
    app.include_router(create_auth_router())
    headers = {"Origin": "http://localhost:3000"}
    if token is not None:
        headers[OPERATOR_TOKEN_HEADER] = token
    return TestClient(app, headers=headers)


def _call(client: TestClient, verb: str, path: str, body):
    return getattr(client, verb)(path, json=body) if body else getattr(client, verb)(path)


# --------------------------------------------------------------------------- #
# 1. Unauthenticated callers cannot initiate or mutate the recovery flow
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("verb,path,body", MUTATION_ROUTES, ids=lambda v: str(v))
def test_no_credential_is_rejected(tmp_path, verb, path, body):
    client = _client(_service(tmp_path), token=None)
    assert _call(client, verb, path, body).status_code == 401


@pytest.mark.parametrize("verb,path,body", MUTATION_ROUTES, ids=lambda v: str(v))
def test_wrong_credential_is_rejected(tmp_path, verb, path, body):
    client = _client(_service(tmp_path), token="not-the-operator-token")
    assert _call(client, verb, path, body).status_code == 401


def test_a_valid_origin_is_not_sufficient_on_its_own(tmp_path):
    """The Origin header is browser-only and forgeable; it must not authorize anything."""
    client = _client(_service(tmp_path), token=None)
    response = client.post(
        "/api/auth/login/start", headers={"Origin": "http://localhost:3000"}
    )
    assert response.status_code == 401


def test_login_url_also_requires_the_operator_credential(tmp_path):
    """It discloses the configured KITE_API_KEY and only an operator needs it."""
    assert _client(_service(tmp_path), token=None).get(
        "/api/auth/login-url"
    ).status_code == 401


def test_routes_fail_closed_when_no_operator_credential_is_configured(tmp_path):
    """An unset RELEASE_MAINTENANCE_TOKEN must close the routes, never open them."""
    client = _client(_service(tmp_path, release_maintenance_token=None))
    for verb, path, body in MUTATION_ROUTES:
        assert _call(client, verb, path, body).status_code == 503


def test_the_credential_is_never_echoed_back(tmp_path):
    client = _client(_service(tmp_path), token="wrong-token")
    response = client.post("/api/auth/login/start")
    assert OPERATOR_TOKEN not in response.text
    assert "wrong-token" not in response.text


# --------------------------------------------------------------------------- #
# 2/4/5. Authorized operator recovery still works
# --------------------------------------------------------------------------- #


def test_browser_oauth_recovery_still_works_for_an_authorized_operator(tmp_path):
    """The retained recovery path stays usable end to end for an authorized operator."""
    service = _service(tmp_path)
    client = _client(service)

    url = client.get("/api/auth/login-url")
    assert url.status_code == 200
    assert "kite.zerodha.com/connect/login" in url.json()["login_url"]
    assert "api_key=apikey" in url.json()["login_url"]

    save_session(
        service.settings.state_dir,
        SessionState(service.trading_date(), "ACCESS", 0.07, 1, 1),
    )
    response = client.post(
        "/api/auth/login", json={"request_token": "rt", "risk_free_rate": 0.07}
    )
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_an_unauthorized_login_is_stopped_before_the_token_exchange(tmp_path):
    """401 rather than 502: rejection happens before any upstream Kite contact."""
    client = _client(_service(tmp_path), token=None)
    response = client.post(
        "/api/auth/login", json={"request_token": "rt", "risk_free_rate": 0.07}
    )
    assert response.status_code == 401


def test_token_broker_authentication_still_works(tmp_path):
    """Broker token present: no TOTP, no stored credentials, straight to rate confirmation."""
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        client_factory=lambda *_: pytest.fail("must not build a Kite login client"),
        external_token_fetcher=lambda: "BROKER_TOKEN",
        external_token_validator=lambda token: None,
        clock=lambda: 1_000,
    )
    progress = coordinator.start("2026-09-01")
    assert progress.step.value == "awaiting_risk_free_rate"
    assert progress.method.value == "shared_session"

    state = coordinator.complete(progress.attempt_id, 0.07)
    assert state.access_token == "BROKER_TOKEN"


def test_an_authorized_start_reaches_the_flow(tmp_path):
    """Proves authorization is the only new gate: the request is served, not blocked."""

    class Flow:
        def __init__(self):
            self.starts = 0

        def start(self, trading_date):
            self.starts += 1
            raise RuntimeError("reached the coordinator")

        def close(self):
            pass

    flow = Flow()
    client = _client(_service(tmp_path, login_flow=flow))
    client.post("/api/auth/login/start")
    assert flow.starts == 1


# --------------------------------------------------------------------------- #
# 3. /api/auth/status stays open for the frontend
# --------------------------------------------------------------------------- #


def test_status_requires_no_credential(tmp_path):
    client = _client(_service(tmp_path), token=None)
    response = client.get("/api/auth/status")
    assert response.status_code == 200
    assert response.json()["configured"] is True


def test_status_never_leaks_the_operator_credential(tmp_path):
    response = _client(_service(tmp_path), token=None).get("/api/auth/status")
    assert OPERATOR_TOKEN not in response.text


# --------------------------------------------------------------------------- #
# 6/7. Stored-credential submission is opt-in and bounded
# --------------------------------------------------------------------------- #


def test_password_fallback_is_blocked_when_the_opt_in_is_disabled(tmp_path):
    """Broker returns nothing and the gate is off: no upstream password submission."""
    built = []

    def factory():
        built.append(1)
        pytest.fail("must not open a Kite login client while the fallback is disabled")

    coordinator = LoginCoordinator(
        _settings(tmp_path, kite_password_login_enabled=False),
        client_factory=factory,
        external_token_fetcher=lambda: None,
        clock=lambda: 1_000,
    )
    with pytest.raises(PasswordLoginDisabledError, match="browser OAuth recovery"):
        coordinator.start("2026-09-01")
    assert built == []


def test_the_disabled_fallback_points_at_the_recovery_path(tmp_path):
    coordinator = LoginCoordinator(
        _settings(tmp_path, kite_password_login_enabled=False),
        client_factory=lambda *_: pytest.fail("no client expected"),
        external_token_fetcher=lambda: None,
        clock=lambda: 1_000,
    )
    with pytest.raises(PasswordLoginDisabledError) as excinfo:
        coordinator.start("2026-09-01")
    assert "/api/auth/login-url" in str(excinfo.value)


def test_repeated_starts_cannot_drive_unbounded_upstream_authentication(tmp_path):
    """Even with the fallback enabled, upstream submissions are capped per window."""
    submissions = []

    class Client:
        def post(self, url, data=None, headers=None):
            submissions.append(url)
            raise RuntimeError("upstream rejected")

        def get(self, url, headers=None):
            raise AssertionError("unexpected GET")

        def close(self):
            pass

    now = {"t": 0}
    coordinator = LoginCoordinator(
        _settings(tmp_path, kite_password_login_enabled=True),
        client_factory=Client,
        external_token_fetcher=lambda: None,
        clock=lambda: now["t"],
    )

    for _ in range(PASSWORD_LOGIN_MAX_PER_WINDOW):
        now["t"] += 10_000
        with pytest.raises(RuntimeError):
            coordinator.start("2026-09-01")

    assert len(submissions) == PASSWORD_LOGIN_MAX_PER_WINDOW

    now["t"] += 10_000
    with pytest.raises(PasswordLoginBudgetError, match="budget for this window is spent"):
        coordinator.start("2026-09-01")
    assert len(submissions) == PASSWORD_LOGIN_MAX_PER_WINDOW, "no further upstream attempt"


def test_the_upstream_budget_recovers_after_its_window(tmp_path):
    submissions = []

    class Client:
        def post(self, url, data=None, headers=None):
            submissions.append(url)
            raise RuntimeError("upstream rejected")

        def get(self, url, headers=None):
            raise AssertionError("unexpected GET")

        def close(self):
            pass

    now = {"t": 0}
    coordinator = LoginCoordinator(
        _settings(tmp_path, kite_password_login_enabled=True),
        client_factory=Client,
        external_token_fetcher=lambda: None,
        clock=lambda: now["t"],
    )
    for _ in range(PASSWORD_LOGIN_MAX_PER_WINDOW):
        now["t"] += 10_000
        with pytest.raises(RuntimeError):
            coordinator.start("2026-09-01")

    now["t"] += PASSWORD_LOGIN_WINDOW_MS
    with pytest.raises(RuntimeError):
        coordinator.start("2026-09-01")
    assert len(submissions) == PASSWORD_LOGIN_MAX_PER_WINDOW + 1


def test_a_broker_token_is_never_charged_against_the_password_budget(tmp_path):
    """The normal path must not be throttled by the fallback's cap."""
    now = {"t": 0}
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        client_factory=lambda *_: pytest.fail("no client expected"),
        external_token_fetcher=lambda: "BROKER_TOKEN",
        external_token_validator=lambda token: None,
        clock=lambda: now["t"],
    )
    for _ in range(PASSWORD_LOGIN_MAX_PER_WINDOW + 2):
        now["t"] += 10_000
        progress = coordinator.start("2026-09-01")
        coordinator.cancel(progress.attempt_id)


# --------------------------------------------------------------------------- #
# Preserved protections
# --------------------------------------------------------------------------- #


def test_cooldown_ttl_and_single_attempt_are_preserved(tmp_path):
    coordinator = LoginCoordinator(
        _settings(tmp_path),
        client_factory=lambda *_: pytest.fail("no client expected"),
        external_token_fetcher=lambda: "BROKER_TOKEN",
        external_token_validator=lambda token: None,
        clock=lambda: 1_000,
    )
    first = coordinator.start("2026-09-01")

    from app.kite.login import KiteLoginError

    with pytest.raises(KiteLoginError, match="already in progress"):
        coordinator.start("2026-09-01")

    coordinator.cancel(first.attempt_id)
    with pytest.raises(KiteLoginError, match="rate limited"):
        coordinator.start("2026-09-01")
