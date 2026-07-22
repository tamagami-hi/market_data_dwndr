"""Operator authentication unit and HTTP integration tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.security.operator_auth import (
    OperatorAuthMiddleware,
    OperatorAuthService,
    create_operator_router,
)


class MutableClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _client(*, max_attempts: int = 3, ttl: int = 60) -> tuple[TestClient, MutableClock]:
    clock = MutableClock()
    service = OperatorAuthService(
        operator_token="A" * 32,
        session_ttl_seconds=ttl,
        login_max_attempts=max_attempts,
        login_window_seconds=60,
        cookie_secure=False,
        allowed_origins=["http://frontend.test"],
        clock=clock,
        token_factory=lambda: "opaque-browser-session",
    )
    app = FastAPI()
    app.state.operator_auth = service
    app.add_middleware(OperatorAuthMiddleware)
    app.include_router(create_operator_router())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/auth/status")
    async def sensitive_status() -> dict[str, bool]:
        return {"authenticated": True}

    @app.post("/api/capture/maintenance")
    async def release_maintenance() -> dict[str, bool]:
        return {"released": True}

    return TestClient(app), clock


def test_unlock_exchanges_operator_token_for_secure_opaque_cookie() -> None:
    client, _ = _client()

    locked = client.get("/api/auth/status")
    unlocked = client.post(
        "/api/operator/unlock",
        headers={"Origin": "http://frontend.test"},
        json={"token": "A" * 32},
    )
    sensitive = client.get("/api/auth/status")

    assert locked.status_code == 401
    assert unlocked.status_code == 200
    assert unlocked.json()["unlocked"] is True
    assert "A" * 32 not in unlocked.text
    cookie = unlocked.headers["set-cookie"]
    assert "operator_session=opaque-browser-session" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert sensitive.status_code == 200


def test_unlock_rejects_untrusted_origin_without_setting_cookie() -> None:
    client, _ = _client()

    response = client.post(
        "/api/operator/unlock",
        headers={"Origin": "http://malicious.test"},
        json={"token": "A" * 32},
    )

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_unlock_is_rate_limited_by_client_after_failed_attempts() -> None:
    client, _ = _client(max_attempts=2)
    headers = {"Origin": "http://frontend.test", "X-Forwarded-For": "203.0.113.9"}

    first = client.post("/api/operator/unlock", headers=headers, json={"token": "B" * 32})
    second = client.post("/api/operator/unlock", headers=headers, json={"token": "C" * 32})
    blocked = client.post("/api/operator/unlock", headers=headers, json={"token": "A" * 32})

    assert first.status_code == 401
    assert second.status_code == 401
    assert blocked.status_code == 429
    assert "A" * 32 not in blocked.text


def test_expired_cookie_cannot_access_sensitive_routes() -> None:
    client, clock = _client(ttl=60)
    response = client.post(
        "/api/operator/unlock",
        headers={"Origin": "http://frontend.test"},
        json={"token": "A" * 32},
    )
    assert response.status_code == 200

    clock.now += 61

    assert client.get("/api/auth/status").status_code == 401


def test_lock_revokes_cookie_and_server_side_session() -> None:
    client, _ = _client()
    client.post(
        "/api/operator/unlock",
        headers={"Origin": "http://frontend.test"},
        json={"token": "A" * 32},
    )

    response = client.post(
        "/api/operator/lock", headers={"Origin": "http://frontend.test"}
    )

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/status").status_code == 401


def test_health_and_release_maintenance_bypass_operator_cookie() -> None:
    client, _ = _client()

    assert client.get("/health").status_code == 200
    assert client.post("/api/capture/maintenance").status_code == 200


def test_operator_status_reports_locked_without_disclosing_configuration() -> None:
    client, _ = _client()

    response = client.get("/api/operator/status")

    assert response.status_code == 200
    assert response.json() == {"unlocked": False}


def test_service_compares_tokens_without_persisting_plaintext() -> None:
    service = OperatorAuthService(
        operator_token="A" * 32,
        session_ttl_seconds=60,
        login_max_attempts=3,
        login_window_seconds=60,
        cookie_secure=False,
        allowed_origins=["http://frontend.test"],
    )

    assert service.verify_operator_token("A" * 32) is True
    assert service.verify_operator_token("B" * 32) is False
    assert "A" * 32 not in repr(service)
    assert not hasattr(service, "operator_token")
