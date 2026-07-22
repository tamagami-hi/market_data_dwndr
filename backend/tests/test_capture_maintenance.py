"""Release-maintenance lease tests (persistence, auth, and lifecycle races)."""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.capture import CaptureController, CaptureError, create_capture_router
from app.capture.maintenance import MaintenanceLeaseStore

TOKEN = "release-token-with-enough-entropy"
TOKEN_HEADER = {"X-Release-Maintenance-Token": TOKEN}
SESSION = SimpleNamespace(
    access_token="ACCESS",
    risk_free_rate=0.07,
    capture_ready=True,
    rate_update_required=False,
)


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        trading_date="2026-07-22",
        index_tables={},
        stock_matrix=None,
        tokens=[],
        skipped_indices=[],
    )


def _settings(state_dir: Path, *, token: str | None = TOKEN) -> SimpleNamespace:
    return SimpleNamespace(
        state_dir=state_dir,
        release_maintenance_token=SecretStr(token) if token is not None else None,
        release_maintenance_ttl_seconds=30,
    )


def _controller(
    state_dir: Path,
    *,
    token: str | None = TOKEN,
    run_fn=None,
    store: MaintenanceLeaseStore | None = None,
) -> CaptureController:
    async def default_run(context, stop_event):
        await stop_event.wait()

    return CaptureController(
        _settings(state_dir, token=token),
        SimpleNamespace(active_session=lambda: SESSION),
        hub=None,
        bootstrap_fn=lambda *args, **kwargs: _context(),
        run_fn=run_fn or default_run,
        maintenance_store=store,
    )


def _client(controller: CaptureController) -> TestClient:
    app = FastAPI()
    app.state.capture_controller = controller
    app.include_router(create_capture_router())
    return TestClient(app)


async def test_acquire_persists_before_waiting_for_capture_flush_and_blocks_racing_start(
    tmp_path,
):
    capture_started = asyncio.Event()
    allow_flush = asyncio.Event()
    writer_flushed = asyncio.Event()

    async def run_until_flushed(context, stop_event):
        capture_started.set()
        await stop_event.wait()
        await allow_flush.wait()
        writer_flushed.set()

    controller = _controller(tmp_path, run_fn=run_until_flushed)
    await controller.start()
    await capture_started.wait()

    acquire_task = asyncio.create_task(controller.acquire_maintenance(TOKEN))
    lease_path = tmp_path / "release-maintenance.json"
    for _ in range(20):
        if lease_path.exists():
            break
        await asyncio.sleep(0)

    assert lease_path.exists()
    assert acquire_task.done() is False

    racing_start = asyncio.create_task(controller.start())
    await asyncio.sleep(0)
    assert racing_start.done() is False

    allow_flush.set()
    lease = await acquire_task

    assert writer_flushed.is_set()
    assert lease.lease_id
    with pytest.raises(CaptureError, match="release maintenance"):
        await racing_start


async def test_valid_lease_survives_controller_restart_and_release_removes_it(tmp_path):
    first = _controller(tmp_path)
    lease = await first.acquire_maintenance(TOKEN)

    restarted = _controller(tmp_path)
    with pytest.raises(CaptureError, match="release maintenance"):
        await restarted.start()

    released = await restarted.release_maintenance(TOKEN, lease.lease_id)

    assert released is True
    assert not (tmp_path / "release-maintenance.json").exists()
    await restarted.start()
    await restarted.stop()


async def test_expired_persisted_lease_is_removed_and_capture_can_start(tmp_path):
    initial_store = MaintenanceLeaseStore(tmp_path, ttl_seconds=30, clock=lambda: 1_000)
    first = _controller(tmp_path, store=initial_store)
    await first.acquire_maintenance(TOKEN)

    expired_store = MaintenanceLeaseStore(tmp_path, ttl_seconds=30, clock=lambda: 31_001)
    restarted = _controller(tmp_path, store=expired_store)

    await restarted.start()

    assert not (tmp_path / "release-maintenance.json").exists()
    await restarted.stop()


def test_maintenance_api_rejects_missing_or_bad_token_without_persisting(tmp_path):
    with _client(_controller(tmp_path)) as client:
        missing = client.post("/api/capture/maintenance")
        bad = client.post(
            "/api/capture/maintenance",
            headers={"X-Release-Maintenance-Token": "incorrect"},
        )

    assert missing.status_code == 401
    assert bad.status_code == 401
    assert not (tmp_path / "release-maintenance.json").exists()


def test_maintenance_api_acquires_and_releases_opaque_lease(tmp_path):
    with _client(_controller(tmp_path)) as client:
        acquired = client.post("/api/capture/maintenance", headers=TOKEN_HEADER)

        assert acquired.status_code == 200
        body = acquired.json()
        assert set(body) == {"lease_id", "expires_at"}
        assert body["lease_id"] not in TOKEN
        assert body["expires_at"].endswith("Z")
        lease_path = tmp_path / "release-maintenance.json"
        assert TOKEN not in lease_path.read_text(encoding="utf-8")
        assert stat.S_IMODE(lease_path.stat().st_mode) == 0o600

        conflict = client.post("/api/capture/maintenance", headers=TOKEN_HEADER)
        assert conflict.status_code == 409

        released = client.delete(
            f"/api/capture/maintenance/{body['lease_id']}", headers=TOKEN_HEADER
        )

    assert released.status_code == 200
    assert released.json() == {"released": True}
    assert not (tmp_path / "release-maintenance.json").exists()


def test_bad_token_cannot_release_an_active_lease(tmp_path):
    with _client(_controller(tmp_path)) as client:
        acquired = client.post("/api/capture/maintenance", headers=TOKEN_HEADER).json()

        response = client.delete(
            f"/api/capture/maintenance/{acquired['lease_id']}",
            headers={"X-Release-Maintenance-Token": "incorrect"},
        )

    assert response.status_code == 401
    assert (tmp_path / "release-maintenance.json").exists()


def test_maintenance_api_is_unavailable_when_secret_is_not_configured(tmp_path):
    with _client(_controller(tmp_path, token=None)) as client:
        response = client.post("/api/capture/maintenance", headers=TOKEN_HEADER)

    assert response.status_code == 503
    assert not (tmp_path / "release-maintenance.json").exists()


def test_maintenance_api_rejects_unknown_lease_id(tmp_path):
    with _client(_controller(tmp_path)) as client:
        response = client.delete(
            "/api/capture/maintenance/not-the-active-lease", headers=TOKEN_HEADER
        )

    assert response.status_code == 404
