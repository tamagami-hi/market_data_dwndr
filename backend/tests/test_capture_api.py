"""Tests for the CaptureController + /api/capture routes (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.capture import CaptureController, create_capture_router


def _fake_context():
    return SimpleNamespace(
        trading_date="2026-07-21",
        index_tables={"NIFTY": object(), "BANKNIFTY": object()},
        stock_matrix=SimpleNamespace(stock_refs=[1, 2, 3]),
        tokens=list(range(210)),
        skipped_indices=[],
    )


_DEFAULT_SESSION = SimpleNamespace(access_token="ACCESS", risk_free_rate=0.07)


def _make_controller(session=_DEFAULT_SESSION):
    started = {"n": 0}

    def fake_bootstrap(settings, access_token, risk_free_rate, *, hub=None):
        started["n"] += 1
        return _fake_context()

    async def fake_run(context, stop_event, *, interval_s=1.0):
        await stop_event.wait()  # stay "running" until stopped

    service = SimpleNamespace(active_session=lambda: session, trading_date=lambda: "2026-07-21")
    controller = CaptureController(
        SimpleNamespace(), service, hub=None, bootstrap_fn=fake_bootstrap, run_fn=fake_run
    )
    return controller, started


def _client(controller) -> TestClient:
    app = FastAPI()
    app.state.capture_controller = controller
    app.include_router(create_capture_router())
    return TestClient(app)


# --- controller unit ---------------------------------------------------------


async def test_controller_start_stop_cycle():
    controller, started = _make_controller()
    assert controller.running is False

    status = await controller.start()
    assert status["running"] is True
    assert status["indices"] == ["NIFTY", "BANKNIFTY"]
    assert status["stocks"] == 3
    assert status["tokens"] == 210
    assert controller.running is True

    # starting twice is rejected
    from app.api.capture import CaptureError

    with pytest.raises(CaptureError, match="already running"):
        await controller.start()
    assert started["n"] == 1

    status = await controller.stop()
    assert status["running"] is False
    assert controller.running is False


async def test_controller_start_requires_login():
    from app.api.capture import CaptureError

    controller, _ = _make_controller(session=None)
    with pytest.raises(CaptureError, match="not logged in"):
        await controller.start()


# --- routes ------------------------------------------------------------------


def test_routes_status_start_stop():
    controller, _ = _make_controller()
    # context-manager form keeps a single event loop so the background capture task
    # stays alive across requests.
    with _client(controller) as client:
        assert client.get("/api/capture/status").json()["running"] is False

        started = client.post("/api/capture/start")
        assert started.status_code == 200
        assert started.json()["running"] is True

        # second start -> 400 (already running)
        assert client.post("/api/capture/start").status_code == 400

        stopped = client.post("/api/capture/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False


def test_routes_degrade_when_unavailable():
    app = FastAPI()
    app.state.capture_controller = None
    app.include_router(create_capture_router())
    client = TestClient(app)
    assert client.get("/api/capture/status").json() == {"available": False, "running": False}
    assert client.post("/api/capture/start").status_code == 503


def test_route_start_not_logged_in_returns_400():
    controller, _ = _make_controller(session=None)
    client = _client(controller)
    r = client.post("/api/capture/start")
    assert r.status_code == 400
    assert "not logged in" in r.json()["detail"]
