"""Tests for the consolidated read-only /api/status endpoint."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.status import collect_status, create_status_router, render_text


def _app(**state) -> TestClient:
    app = FastAPI()
    app.include_router(create_status_router())
    for key, value in state.items():
        setattr(app.state, key, value)
    return TestClient(app)


def _running_state():
    session = SimpleNamespace(
        status=lambda: {
            "configured": True,
            "authenticated": True,
            "trading_date": "2026-07-22",
            "market_phase": "capture_window",
            "risk_free_rate": 0.053324,
            "risk_free_rate_as_of": "2026-07-22",
            "external_token_source_configured": True,
        }
    )
    automation = SimpleNamespace(
        status=lambda: {
            "phase": "capture_window",
            "last_action": "START_CAPTURE",
            "last_error": None,
            "compression": {
                "phase": "done",
                "files_done": 2,
                "files_total": 2,
                "bytes_done": 550_000,
                "bytes_total": 550_000,
                "zst_bytes": 50_000,
                "ratio": 11.0,
                "threads": 6,
            },
        }
    )
    controller = SimpleNamespace(
        status=lambda: {
            "available": True,
            "running": True,
            "trading_date": "2026-07-22",
            "indices": ["NIFTY", "BANKNIFTY"],
            "stocks": 180,
            "tokens": 1600,
            "skipped_indices": [],
            "error": None,
        },
        monitor_snapshot=lambda: {
            "global": {"tokens": 1600, "fps": 1.0, "captures": 1234, "disk_bytes": 2_500_000_000},
            "per_underlying": [
                {
                    "underlying": "NIFTY",
                    "connected": True,
                    "heartbeat_ok": True,
                    "frames_written": 1234,
                    "file_bytes": 12_300_000,
                    "last_tick_ms": 1_753_000_000_000,
                    "unmatched": 0,
                }
            ],
        },
    )
    return session, automation, controller


def test_status_json_when_running_merges_all_sources():
    session, automation, controller = _running_state()
    client = _app(
        session_service=session, daily_automation=automation, capture_controller=controller
    )

    body = client.get("/api/status").json()

    assert body["configured"] is True
    assert body["session"]["authenticated"] is True
    assert body["session"]["risk_free_rate"] == 0.053324
    assert body["capture"]["running"] is True
    assert body["monitor"]["global"]["tokens"] == 1600
    assert body["monitor"]["per_underlying"][0]["underlying"] == "NIFTY"
    assert body["compression"]["phase"] == "done"
    assert isinstance(body["generated_at"], int)


def test_status_text_renders_dashboard():
    session, automation, controller = _running_state()
    client = _app(
        session_service=session, daily_automation=automation, capture_controller=controller
    )

    resp = client.get("/api/status", params={"format": "text"})

    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert "SESSION" in text and "authenticated=yes" in text
    assert "risk_free_rate=0.053324" in text
    assert "COMPRESSION done" in text
    assert "PER-UNDERLYING" in text and "NIFTY" in text
    assert "GLOBAL" in text


def test_status_unconfigured_backend():
    client = _app()  # no services on app.state

    body = client.get("/api/status").json()
    assert body["configured"] is False
    assert body["capture"] == {"available": False, "running": False}

    text = client.get("/api/status", params={"format": "text"}).text
    assert "not configured" in text


def test_collect_status_handles_capture_not_running():
    session, automation, _ = _running_state()
    controller = SimpleNamespace(
        status=lambda: {"available": True, "running": False, "indices": [], "tokens": 0,
                        "trading_date": None, "stocks": 0, "skipped_indices": [], "error": None},
        monitor_snapshot=lambda: None,
    )
    snapshot = collect_status(
        SimpleNamespace(
            session_service=session, daily_automation=automation, capture_controller=controller
        )
    )
    assert snapshot["monitor"] is None
    # renders without a live monitor section
    assert "no live telemetry" in render_text(snapshot)
