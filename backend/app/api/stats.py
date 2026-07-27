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


def _market_phase(session_service) -> str | None:
    if session_service is None:
        return None
    try:
        status = session_service.status()
        if isinstance(status, dict):
            return status.get("market_phase")
    except Exception:  # noqa: BLE001
        return None
    return None


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = str(value).split(":")
    return int(hour), int(minute)


def _refresh_window(settings, session_service, capture_running: bool) -> dict:
    """Describe when the dashboard should actively refresh.

    The monitor only changes while (a) capture is running, or (b) the pre-open broker
    auth/token window is open (``AUTH_POLL_START``..``AUTH_POLL_END``) and the new
    session is being established. Outside both, the last session's numbers are final,
    so the frontend can idle instead of polling every few seconds.
    """
    auth_start = getattr(settings, "auth_poll_start", "08:30") if settings else "08:30"
    auth_end = getattr(settings, "auth_poll_end", "09:00") if settings else "09:00"
    info: dict = {
        "auth_poll_start": auth_start,
        "auth_poll_end": auth_end,
        "in_auth_window": False,
        "should_refresh": bool(capture_running),
    }
    if session_service is None:
        return info
    try:
        calendar = session_service.calendar
        local_time = calendar.local_dt(now_ms()).time().replace(tzinfo=None)
        start_h, start_m = _parse_hhmm(auth_start)
        end_h, end_m = _parse_hhmm(auth_end)
        minutes = local_time.hour * 60 + local_time.minute
        in_auth = (start_h * 60 + start_m) <= minutes < (end_h * 60 + end_m)
        info["in_auth_window"] = bool(in_auth)
        info["should_refresh"] = bool(capture_running or in_auth)
        info["local_time"] = local_time.strftime("%H:%M")
    except Exception:  # noqa: BLE001 - a scheduling hint must never break the read
        logger.debug("refresh-window computation failed", exc_info=True)
    return info


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
        settings = None
        state_dir = None
        expected_frames = 23_400

    trading_date = _trading_date(session_service)

    payload: dict = {
        "generated_at": now_ms(),
        "capture_running": False,
        "trading_date": trading_date,
        "market_phase": _market_phase(session_service),
        "expected_frames_per_session": expected_frames,
        "monitor": None,
        "monitor_persisted": False,
        # Trading date the monitor payload belongs to — may be an EARLIER session when
        # today's capture has not started, so the UI can label retained data honestly.
        "monitor_trading_date": None,
        "session_history": [],
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
                payload["monitor_trading_date"] = trading_date
        except Exception:  # noqa: BLE001 - telemetry must never break the read
            logger.debug("monitor snapshot failed for /api/stats", exc_info=True)

    payload["refresh_window"] = _refresh_window(
        settings, session_service, bool(payload["capture_running"])
    )

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
        try:
            payload["session_history"] = stats_store.session_history(state_dir)
        except Exception:  # noqa: BLE001
            logger.debug("session history read failed", exc_info=True)
        if payload["monitor"] is None:
            # Prefer today's snapshot; otherwise fall back to the most recent session on
            # disk so the dashboard retains the LAST session's data instead of blanking.
            try:
                persisted = (
                    stats_store.load_capture_snapshot(state_dir, trading_date)
                    if trading_date is not None
                    else None
                )
                if persisted is not None:
                    payload["monitor"] = persisted
                    payload["monitor_persisted"] = True
                    payload["monitor_trading_date"] = trading_date
                else:
                    latest = stats_store.latest_capture_snapshot(state_dir)
                    if latest is not None:
                        payload["monitor"] = latest[1]
                        payload["monitor_persisted"] = True
                        payload["monitor_trading_date"] = latest[0]
            except Exception:  # noqa: BLE001
                logger.debug("persisted capture snapshot read failed", exc_info=True)

    return payload


def create_stats_router() -> APIRouter:
    router = APIRouter(tags=["stats"])

    @router.get("/api/stats")
    async def stats(request: Request) -> dict:
        return collect_stats(request.app.state)

    return router
