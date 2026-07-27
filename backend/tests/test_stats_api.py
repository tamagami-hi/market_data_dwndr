"""Tests for the aggregated /api/stats endpoint."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stats as stats_api
from app.api.stats import collect_stats, create_stats_router
from app.ops import stats_store


def _session(trading_date="2026-07-22"):
    return SimpleNamespace(status=lambda: {"trading_date": trading_date})


def _automation(compression):
    return SimpleNamespace(status=lambda: {"phase": "capture_window", "compression": compression})


def _running_controller():
    return SimpleNamespace(
        running=True,
        monitor_snapshot=lambda: {
            "global": {"tokens": 1600, "fps": 1.0, "frame_loss_pct": 12.5, "uptime_ms": 60_000},
            "per_underlying": [{"underlying": "NIFTY", "frames_written": 1234}],
        },
    )


def _patch_settings(monkeypatch, tmp_path, **extra):
    monkeypatch.setattr(
        stats_api,
        "get_settings",
        lambda: SimpleNamespace(
            stats_dir=tmp_path,
            expected_frames_per_session=23_400,
            auth_poll_start=extra.get("auth_poll_start", "08:30"),
            auth_poll_end=extra.get("auth_poll_end", "09:00"),
        ),
    )


def test_stats_exposes_session_history(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    stats_store.record_session_summary(
        tmp_path, {"trading_date": "2026-07-24", "grid_seconds_lost": 7}
    )
    payload = collect_stats(SimpleNamespace(session_service=_session()))
    assert [r["trading_date"] for r in payload["session_history"]] == ["2026-07-24"]
    assert payload["session_history"][0]["grid_seconds_lost"] == 7


def test_stats_retains_last_session_snapshot_when_today_has_none(monkeypatch, tmp_path):
    """With no capture today, the dashboard must fall back to the newest prior session."""
    _patch_settings(monkeypatch, tmp_path)
    stats_store.write_capture_snapshot(
        tmp_path, "2026-07-24", {"global": {"captures": 42}, "per_underlying": []}
    )
    payload = collect_stats(SimpleNamespace(session_service=_session("2026-07-27")))
    assert payload["monitor_persisted"] is True
    assert payload["monitor_trading_date"] == "2026-07-24"  # an EARLIER session
    assert payload["monitor"]["global"]["captures"] == 42


def test_stats_prefers_todays_snapshot_over_older(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    stats_store.write_capture_snapshot(tmp_path, "2026-07-24", {"global": {"captures": 1}})
    stats_store.write_capture_snapshot(tmp_path, "2026-07-27", {"global": {"captures": 2}})
    payload = collect_stats(SimpleNamespace(session_service=_session("2026-07-27")))
    assert payload["monitor_trading_date"] == "2026-07-27"
    assert payload["monitor"]["global"]["captures"] == 2


def test_refresh_window_should_refresh_while_capture_runs(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    payload = collect_stats(
        SimpleNamespace(
            session_service=_session(),
            capture_controller=_running_controller(),
        )
    )
    window = payload["refresh_window"]
    assert window["should_refresh"] is True
    assert window["auth_poll_start"] == "08:30"
    assert window["auth_poll_end"] == "09:00"


def test_refresh_window_reports_auth_window_from_calendar(monkeypatch, tmp_path):
    """Inside 08:30-09:00 the dashboard should refresh even with capture stopped."""
    _patch_settings(monkeypatch, tmp_path)

    class _Cal:
        def local_dt(self, _ms):
            from datetime import datetime

            return datetime(2026, 7, 27, 8, 45)

    service = SimpleNamespace(status=lambda: {"trading_date": "2026-07-27"}, calendar=_Cal())
    payload = collect_stats(SimpleNamespace(session_service=service))
    assert payload["refresh_window"]["in_auth_window"] is True
    assert payload["refresh_window"]["should_refresh"] is True


def test_refresh_window_idle_outside_windows(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)

    class _Cal:
        def local_dt(self, _ms):
            from datetime import datetime

            return datetime(2026, 7, 27, 22, 0)  # after hours

    service = SimpleNamespace(status=lambda: {"trading_date": "2026-07-27"}, calendar=_Cal())
    payload = collect_stats(SimpleNamespace(session_service=service))
    assert payload["refresh_window"]["in_auth_window"] is False
    assert payload["refresh_window"]["should_refresh"] is False


def test_stats_merges_live_monitor_and_compression(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    compression = {"phase": "done", "ratio": 11.0, "throughput_mbps": 42.0, "avg_file_ms": 120.0}
    app_state = SimpleNamespace(
        session_service=_session(),
        daily_automation=_automation(compression),
        capture_controller=_running_controller(),
    )
    out = collect_stats(app_state)

    assert out["capture_running"] is True
    assert out["monitor"]["global"]["tokens"] == 1600
    assert out["monitor_persisted"] is False
    assert out["compression"]["ratio"] == 11.0
    assert out["expected_frames_per_session"] == 23_400
    assert out["trading_date"] == "2026-07-22"


def test_stats_includes_persisted_compression_averages(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    # Seed two persisted sweeps.
    for date, raw, zst, elapsed in (("2026-07-20", 1000, 200, 400), ("2026-07-21", 2000, 400, 600)):
        stats_store.record_compression(
            tmp_path,
            SimpleNamespace(
                compressed=["a", "b"],
                total_raw_bytes=raw,
                total_zst_bytes=zst,
                ratio=raw / zst,
                elapsed_ms=elapsed,
                avg_file_ms=elapsed / 2,
                throughput_mbps=(raw / 1e6) / (elapsed / 1000.0),
            ),
            trading_date=date,
        )
    app_state = SimpleNamespace(
        session_service=_session(),
        daily_automation=_automation(None),
        capture_controller=_running_controller(),
    )
    out = collect_stats(app_state)
    hist = out["compression_history"]
    assert hist["samples"] == 2
    assert hist["avg_ratio"] == 5.0
    assert hist["last"]["trading_date"] == "2026-07-21"


def test_stats_falls_back_to_persisted_snapshot_when_not_running(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    stats_store.write_capture_snapshot(
        tmp_path, "2026-07-22", {"global": {"fps": 0.0}, "per_underlying": []}
    )
    controller = SimpleNamespace(running=False, monitor_snapshot=lambda: None)
    app_state = SimpleNamespace(
        session_service=_session(),
        daily_automation=_automation(None),
        capture_controller=controller,
    )
    out = collect_stats(app_state)
    assert out["capture_running"] is False
    assert out["monitor_persisted"] is True
    assert out["monitor"]["global"]["fps"] == 0.0


def test_stats_endpoint_smoke(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    app = FastAPI()
    app.include_router(create_stats_router())
    app.state.session_service = _session()
    app.state.daily_automation = _automation({"phase": "idle"})
    app.state.capture_controller = _running_controller()
    client = TestClient(app)

    body = client.get("/api/stats").json()
    assert body["capture_running"] is True
    assert isinstance(body["generated_at"], int)
    assert body["monitor"]["per_underlying"][0]["underlying"] == "NIFTY"
