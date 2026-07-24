"""Tests for the consolidated /health endpoint (ok / degraded / dead + HTTP codes)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health import CODE_DEAD, CODE_OK, CODE_STALE, build_health, create_health_router


def _state(**kwargs) -> SimpleNamespace:
    """App-state stub; unspecified attrs default to None via SimpleNamespace lookup."""
    kwargs.setdefault("capture_controller", None)
    kwargs.setdefault("session_service", None)
    return SimpleNamespace(**kwargs)


def _controller(*, running=False, has_failed=False, snapshot=None):
    return SimpleNamespace(
        running=running,
        has_failed=has_failed,
        monitor_snapshot=lambda: snapshot,
    )


def _check(payload: dict, component: str) -> dict:
    return next(c for c in payload["checks"] if c["component"] == component)


# -- build_health: pure-function state matrix ------------------------------- #


def test_unconfigured_backend_is_ok_and_idle():
    payload, status = build_health(_state())
    assert status == CODE_OK
    assert payload["status"] == "ok"
    assert _check(payload, "process")["status"] == "ok"
    assert _check(payload, "capture_task")["status"] == "idle"
    assert _check(payload, "data_freshness")["status"] == "idle"


def test_running_and_fresh_is_ok():
    snap = {"global": {"stale": False, "degraded": False, "data_age_ms": 300, "reconnects": 0}}
    payload, status = build_health(
        _state(capture_controller=_controller(running=True, snapshot=snap))
    )
    assert status == CODE_OK
    assert payload["status"] == "ok"
    assert _check(payload, "capture_task")["status"] == "ok"
    fresh = _check(payload, "data_freshness")
    assert fresh["status"] == "ok"
    assert fresh["data_age_ms"] == 300


def test_running_but_no_telemetry_is_ok_warming_up():
    payload, status = build_health(
        _state(capture_controller=_controller(running=True, snapshot=None))
    )
    assert status == CODE_OK
    assert payload["status"] == "ok"
    assert _check(payload, "data_freshness")["status"] == "ok"


def test_stale_feed_is_degraded_but_http_200():
    snap = {
        "global": {"stale": True, "degraded": True, "data_age_ms": 42_000, "reconnects": 3}
    }
    payload, status = build_health(
        _state(capture_controller=_controller(running=True, snapshot=snap))
    )
    # Degraded must NOT flip HTTP to 503 (would restart-loop on staleness).
    assert status == CODE_OK
    assert payload["status"] == "degraded"
    fresh = _check(payload, "data_freshness")
    assert fresh["status"] == "stale"
    assert fresh["code"] == CODE_STALE
    assert fresh["data_age_ms"] == 42_000
    assert fresh["reconnects"] == 3


def test_crashed_capture_task_is_dead_and_http_503():
    payload, status = build_health(
        _state(capture_controller=_controller(running=False, has_failed=True))
    )
    assert status == CODE_DEAD
    assert payload["status"] == "dead"
    cap = _check(payload, "capture_task")
    assert cap["status"] == "dead"
    assert cap["code"] == CODE_DEAD


def test_dead_takes_precedence_over_stale():
    snap = {"global": {"stale": True, "degraded": True}}
    payload, status = build_health(
        _state(capture_controller=_controller(running=True, has_failed=True, snapshot=snap))
    )
    assert status == CODE_DEAD
    assert payload["status"] == "dead"


def test_market_phase_surfaced_from_session_service():
    service = SimpleNamespace(status=lambda: {"market_phase": "capture_window"})
    payload, _ = build_health(_state(session_service=service))
    assert payload["market_phase"] == "capture_window"


def test_probe_never_raises_when_snapshot_errors():
    def _boom():
        raise RuntimeError("telemetry exploded")

    controller = SimpleNamespace(running=True, has_failed=False, monitor_snapshot=_boom)
    payload, status = build_health(_state(capture_controller=controller))
    assert status == CODE_OK  # swallowed -> treated as warming up, not a fault


# -- router: HTTP status wiring --------------------------------------------- #


def _client(state: SimpleNamespace) -> TestClient:
    app = FastAPI()
    app.include_router(create_health_router())
    app.state.capture_controller = state.capture_controller
    app.state.session_service = state.session_service
    return TestClient(app)


def test_router_returns_200_when_ok():
    resp = _client(_state()).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_router_returns_503_when_dead():
    resp = _client(
        _state(capture_controller=_controller(has_failed=True))
    ).get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "dead"


def test_router_returns_200_when_degraded():
    snap = {"global": {"stale": True}}
    resp = _client(
        _state(capture_controller=_controller(running=True, snapshot=snap))
    ).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
