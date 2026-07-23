"""Aggregated statistics API for the monitor dashboard.

``GET /api/stats`` merges three sources into one payload the frontend renders:

- **live monitor** telemetry (per-underlying + global) while capture is running;
- **current compression** progress (the last EOD sweep state from automation);
- **persisted compression history** averages + the latest daily capture snapshot
  (from ``_state/stats/``) so the dashboard still shows meaningful numbers
  after hours / before capture starts.

Read-only and secret-free, like ``/api/status``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.config import get_settings
from app.ops import stats_store
from app.session import now_ms

logger = logging.getLogger(__name__)


def _trading_date(session_service) -> str | None:
    if session_service is None:
        return None
    try:
        status = session_service.status()
        if isinstance(status, dict) and status.get("trading_date"):
            return status["trading_date"]
    except Exception:  # noqa: BLE001 - status must never raise
        pass
    try:
        return session_service.trading_date()
    except Exception:  # noqa: BLE001
        return None


def collect_stats(app_state) -> dict:
    """Assemble the dashboard stats payload from app state + persisted history."""
    controller = getattr(app_state, "capture_controller", None)
    automation = getattr(app_state, "daily_automation", None)
    session_service = getattr(app_state, "session_service", None)

    try:
        settings = get_settings()
        state_dir = settings.stats_dir
        expected_frames = getattr(settings, "expected_frames_per_session", 23_400)
    except Exception:  # noqa: BLE001 - settings unavailable in some test contexts
        state_dir = None
        expected_frames = 23_400

    trading_date = _trading_date(session_service)

    payload: dict = {
        "generated_at": now_ms(),
        "capture_running": False,
        "trading_date": trading_date,
        "expected_frames_per_session": expected_frames,
        "monitor": None,
        "monitor_persisted": False,
        "compression": None,
        "compression_history": {
            "samples": 0,
            "avg_ratio": 0.0,
            "avg_total_elapsed_ms": 0.0,
            "avg_file_ms": 0.0,
            "avg_throughput_mbps": 0.0,
            "last": None,
        },
    }

    # Live monitor telemetry (only while capture runs).
    if controller is not None:
        try:
            payload["capture_running"] = bool(controller.running)
            monitor = controller.monitor_snapshot()
            if monitor is not None:
                payload["monitor"] = monitor
        except Exception:  # noqa: BLE001 - telemetry must never break the read
            logger.debug("monitor snapshot failed for /api/stats", exc_info=True)

    # Current compression progress from the automation service.
    if automation is not None:
        try:
            auto = automation.status()
            if isinstance(auto, dict):
                payload["compression"] = auto.get("compression")
        except Exception:  # noqa: BLE001
            logger.debug("automation status failed for /api/stats", exc_info=True)

    # Persisted history + fallback snapshot.
    if state_dir is not None:
        try:
            payload["compression_history"] = stats_store.compression_averages(state_dir)
        except Exception:  # noqa: BLE001
            logger.debug("compression history read failed", exc_info=True)
        if payload["monitor"] is None and trading_date is not None:
            try:
                persisted = stats_store.load_capture_snapshot(state_dir, trading_date)
                if persisted is not None:
                    payload["monitor"] = persisted
                    payload["monitor_persisted"] = True
            except Exception:  # noqa: BLE001
                logger.debug("persisted capture snapshot read failed", exc_info=True)

    return payload


def create_stats_router() -> APIRouter:
    router = APIRouter(tags=["stats"])

    @router.get("/api/stats")
    async def stats(request: Request) -> dict:
        return collect_stats(request.app.state)

    return router
