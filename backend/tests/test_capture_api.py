"""Tests for the CaptureController + /api/capture routes (no network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.capture import CaptureController, CaptureError, create_capture_router


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


async def test_controller_start_requires_fresh_risk_free_rate():
    from app.api.capture import CaptureError

    stale = SimpleNamespace(
        access_token="ACCESS",
        risk_free_rate=0.065,
        capture_ready=False,
        rate_update_required=True,
    )
    controller, _ = _make_controller(session=stale)

    with pytest.raises(CaptureError, match="risk-free rate update is required"):
        await controller.start()


async def test_controller_redacts_capture_task_failures(caplog):
    async def failing_run(_context, _stop_event):
        raise RuntimeError("ACCESS_TOKEN_MUST_NOT_ESCAPE")

    controller, _ = _make_controller()
    controller._run_fn = failing_run

    await controller.start()
    await asyncio.sleep(0)

    assert controller.status()["error"] == "capture task failed; inspect backend logs"
    assert "ACCESS_TOKEN_MUST_NOT_ESCAPE" not in caplog.text
    with pytest.raises(CaptureError, match="did not flush and stop safely"):
        await controller.stop()
    with pytest.raises(CaptureError, match="previous capture failed"):
        await controller.start()


# --- routes ------------------------------------------------------------------


def test_status_route_is_read_only():
    controller, _ = _make_controller()
    client = _client(controller)

    assert client.get("/api/capture/status").json()["running"] is False
    assert client.post("/api/capture/start").status_code == 404
    assert client.post("/api/capture/stop").status_code == 404


def test_routes_degrade_when_unavailable():
    app = FastAPI()
    app.state.capture_controller = None
    app.include_router(create_capture_router())
    client = TestClient(app)
    assert client.get("/api/capture/status").json() == {"available": False, "running": False}



async def test_auth_failure_invalidates_session_and_allows_automatic_restart():
    from app.kite.errors import KiteAuthenticationError

    session = SimpleNamespace(access_token="EXPIRED", risk_free_rate=0.07)
    holder = {"session": session}
    invalidated: list[str] = []

    class Sessions:
        def active_session(self):
            return holder["session"]

        def invalidate_active_session(self, expected_access_token):
            invalidated.append(expected_access_token)
            if holder["session"].access_token != expected_access_token:
                return False
            holder["session"] = None
            return True

    runs = {"count": 0}

    async def auth_then_wait(_context, stop_event):
        runs["count"] += 1
        if runs["count"] == 1:
            raise KiteAuthenticationError("secret details")
        await stop_event.wait()

    controller = CaptureController(
        SimpleNamespace(),
        Sessions(),
        hub=None,
        bootstrap_fn=lambda *_args, **_kwargs: _fake_context(),
        run_fn=auth_then_wait,
    )

    await controller.start()
    await asyncio.sleep(0)

    assert invalidated == ["EXPIRED"]
    assert holder["session"] is None
    assert controller.status()["error"] == (
        "broker session expired; waiting for automatic token refresh"
    )

    holder["session"] = SimpleNamespace(access_token="FRESH", risk_free_rate=0.07)
    await controller.start()
    assert controller.running is True
    await controller.stop()
