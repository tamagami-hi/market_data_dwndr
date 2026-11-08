"""Aggregated statistics API for the monitor dashboard.

``GET /api/stats`` merges three sources into one payload the frontend renders:

- **live monitor** telemetry (per-underlying + global) while capture is running;
- **current compression** progress (the last EOD sweep state from automation);
- **persisted compression history** averages + the latest daily capture snapshot
  (from ``_state/stats/``) so the dashboard still shows meaningful numbers
  after hours / before capture starts.

Every session-history row (and the current day in ``session_outcome``) is described by
:func:`app.ops.session_outcome.describe_session`: one primary ``completeness_pct``, an
``outcome`` class, every missing second attributed to a cause, the day's incidents and
the archive reconciliation figure. ``GET /api/stats/sessions`` pages through the full,
never-pruned history; ``GET /api/stats/incidents/{date}`` returns one day's raw ledger.

Read-only and secret-free, like ``/api/status``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.config import Settings, get_settings
from app.ops import stats_store
from app.ops.session_report import describe_monitor, describe_rows, incident_report
from app.session import now_ms

logger = logging.getLogger(__name__)

# ``/api/stats`` is polled every few seconds, so it carries only the recent history.
RECENT_SESSIONS = 30
SESSION_PAGE_DEFAULT = 50
SESSION_PAGE_MAX = 500
ISO_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


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
    # Falls back to the field defaults rather than to literal clock times, so the auth
    # window always reflects configuration.
    auth_start = getattr(settings, "auth_poll_start", None) if settings else None
    auth_end = getattr(settings, "auth_poll_end", None) if settings else None
    if auth_start is None or auth_end is None:
        defaults = Settings.model_fields
        auth_start = auth_start or defaults["auth_poll_start"].default
        auth_end = auth_end or defaults["auth_poll_end"].default
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
        current = now_ms()
        local_time = calendar.local_dt(current).time().replace(tzinfo=None)
        special_lookup = getattr(calendar, "special_session", None)
        special = special_lookup(current) if callable(special_lookup) else None
        if special is not None:
            # A special session has its own token window (token_fetch_at..capture start).
            auth_start = special.token_fetch_at
            auth_end = special.capture_start_time.strftime("%H:%M")
            info["auth_poll_start"], info["auth_poll_end"] = auth_start, auth_end
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
        expected_frames = getattr(settings, "expected_frames_per_session", 0)
    except Exception:  # noqa: BLE001 - settings unavailable in some test contexts
        settings = None
        state_dir = None
        # No configuration means no session window, and therefore no defensible frame
        # baseline. Reporting 0 makes the absence visible; inventing a number would put a
        # fabricated market schedule into the loss percentages.
        expected_frames = 0

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
            payload["session_history"] = describe_rows(
                stats_store.session_history(state_dir, RECENT_SESSIONS),
                settings=settings,
                stats_dir=state_dir,
                now_ms=payload["generated_at"],
            )
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

    _attach_day_outcome(payload, settings, state_dir)
    return payload


def _attach_day_outcome(payload: dict, settings, state_dir) -> None:
    """``session_outcome`` + ``incident_ledger`` for the monitor's (or today's) date."""
    payload["session_outcome"] = None
    payload["incident_ledger"] = None
    day = payload.get("monitor_trading_date") or payload.get("trading_date")
    if state_dir is None or not day or not stats_store.is_iso_date(str(day)):
        return
    now = payload["generated_at"]
    try:
        payload["session_outcome"] = describe_monitor(
            payload.get("monitor"), day, settings=settings, stats_dir=state_dir, now_ms=now
        )
        payload["incident_ledger"] = incident_report(
            state_dir, day, settings=settings, now_ms=now
        )
    except Exception:  # noqa: BLE001 - an explanation must never break the read
        logger.debug("session outcome failed for /api/stats", exc_info=True)


def _settings_or_none():
    try:
        return get_settings()
    except Exception:  # noqa: BLE001 - settings unavailable in some test contexts
        return None


def collect_session_page(limit: int, before: str | None) -> dict:
    """One page of the full session history, described like ``session_history``."""
    settings = _settings_or_none()
    state_dir = getattr(settings, "stats_dir", None) if settings is not None else None
    if state_dir is None:
        return {"sessions": [], "total": 0, "next_before": None}
    page = stats_store.session_history_page(state_dir, limit=limit, before=before)
    return {
        **page,
        "sessions": describe_rows(
            page["sessions"], settings=settings, stats_dir=state_dir, now_ms=now_ms()
        ),
    }


def collect_incidents(trading_date: str) -> dict:
    settings = _settings_or_none()
    state_dir = getattr(settings, "stats_dir", None) if settings is not None else None
    return incident_report(state_dir, trading_date, settings=settings, now_ms=now_ms())


def create_stats_router() -> APIRouter:
    router = APIRouter(tags=["stats"])

    @router.get("/api/stats")
    async def stats(request: Request) -> dict:
        return collect_stats(request.app.state)

    @router.get("/api/stats/sessions")
    async def sessions(
        limit: Annotated[int, Query(ge=1, le=SESSION_PAGE_MAX)] = SESSION_PAGE_DEFAULT,
        before: Annotated[
            str | None, Query(pattern=ISO_DATE_PATTERN, description="YYYY-MM-DD, exclusive")
        ] = None,
    ) -> dict:
        """The full session history, newest first, ``limit`` rows before ``before``."""
        if before is not None and not stats_store.is_iso_date(before):
            raise HTTPException(status_code=422, detail="before must be a valid YYYY-MM-DD date")
        return await asyncio.to_thread(collect_session_page, limit, before)

    @router.get("/api/stats/incidents/{trading_date}")
    async def incidents_for_day(
        trading_date: Annotated[str, Path(pattern=ISO_DATE_PATTERN)],
    ) -> dict:
        """One trading date's incident ledger and its timeline analysis."""
        if not stats_store.is_iso_date(trading_date):
            raise HTTPException(status_code=422, detail="trading_date must be YYYY-MM-DD")
        return await asyncio.to_thread(collect_incidents, trading_date)

    return router
