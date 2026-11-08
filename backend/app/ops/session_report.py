"""Glue between persisted stats and the dashboard's per-day session descriptors.

Loads what :func:`app.ops.session_outcome.describe_session` needs for a trading date —
the session window that applied (regular or operator special), the incident ledger and
the archive reconciliation record — and applies it to history rows and to the live (or
last persisted) monitor payload. Every function here is read-only and tolerant: a day
whose extras cannot be loaded is returned as recorded rather than failing the read.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time

from app.ops import incidents, stats_store
from app.ops.incident_timeline import analyze_incidents
from app.ops.session_outcome import describe_session

logger = logging.getLogger(__name__)

DEFAULT_TZ = "Asia/Kolkata"
# The schedule artifact every TickVault dataset is owed against (see bootstrap): all of
# them carry a derivatives leg, so the derivatives session is the day's owed window.
_WINDOW_ARTIFACT = "STOCKS"

# Monitor ``global`` keys that make up a session summary for ``describe_session``.
SUMMARY_KEYS = (
    "captured_seconds",
    "scheduled_seconds",
    "scheduled_seconds_elapsed",
    "scheduled_loss_pct",
    "missing_seconds",
    "stale_feed_seconds",
    "awaiting_first_tick_seconds",
    "write_path_seconds",
    "unclassified_seconds",
    "restart_downtime_seconds",
    "not_running_seconds",
    "downtime_seconds",
    "escalations",
    "session_loss_pct",
    "data_loss_pct",
    "frame_loss_pct",
)


def _tz_name(settings) -> str:
    return str(getattr(settings, "timezone", None) or DEFAULT_TZ)


def session_window_for(settings, trading_date: str) -> dict | None:
    """The owed window(s) on ``trading_date``: regular, or the operator special session.

    ``None`` when the date or the settings cannot be resolved. On a non-trading day the
    window list is empty and ``scheduled_seconds`` is 0.
    """
    if settings is None or not stats_store.is_iso_date(trading_date):
        return None
    from app.ops.sessions import SESSION_EQUITY_DERIV, build_session_registry

    day = date.fromisoformat(trading_date)
    registry = build_session_registry(settings, {_WINDOW_ARTIFACT: SESSION_EQUITY_DERIV})
    calendar = registry.calendar
    noon_ms = int(datetime.combine(day, time(12, 0), tzinfo=calendar.tz).timestamp() * 1000)
    session = registry.session_for(_WINDOW_ARTIFACT).on_day(calendar, day)
    windows = registry.scheduled_windows_on(_WINDOW_ARTIFACT, day)
    special = calendar.special_session(noon_ms)
    return {
        "open": session.open_at.strftime("%H:%M"),
        "close": session.close_at.strftime("%H:%M"),
        "is_special": special is not None,
        "note": (special.note or None) if special is not None else None,
        "trading_day": bool(calendar.is_trading_day(noon_ms)),
        "scheduled_seconds": int(sum((end - start) // 1000 for start, end in windows)),
        "windows": [[int(start), int(end)] for start, end in windows],
    }


def safe_session_window(settings, trading_date: str) -> dict | None:
    """:func:`session_window_for` that logs and returns ``None`` instead of raising."""
    try:
        return session_window_for(settings, trading_date)
    except Exception:  # noqa: BLE001 - a schedule lookup must never break a stats read
        logger.debug("session window lookup failed for %s", trading_date, exc_info=True)
        return None


def _ledger(stats_dir, trading_date: str) -> dict | None:
    if stats_dir is None or not stats_store.is_iso_date(trading_date):
        return None
    if not incidents.ledger_exists(stats_dir, trading_date):
        return None
    return incidents.load_incidents(stats_dir, trading_date)


def describe_rows(
    rows: Iterable[Mapping],
    *,
    settings,
    stats_dir: str | os.PathLike[str] | None,
    now_ms: int,
) -> list[dict]:
    """Session-history rows with outcome, causes, incidents and archive truth added."""
    rows = list(rows)
    dates = {str(r.get("trading_date")) for r in rows}
    reconciliations = (
        stats_store.reconciliation_for_dates(stats_dir, dates) if stats_dir is not None else {}
    )
    described: list[dict] = []
    for row in rows:
        trading_date = str(row.get("trading_date"))
        try:
            described.append(
                describe_session(
                    row,
                    ledger=_ledger(stats_dir, trading_date),
                    reconciliation=reconciliations.get(trading_date),
                    window=row.get("session_window") or safe_session_window(settings, trading_date),
                    now_ms=now_ms,
                    tz_name=_tz_name(settings),
                )
            )
        except Exception:  # noqa: BLE001 - one bad row must not blank the history
            logger.debug("could not describe session %s", trading_date, exc_info=True)
            described.append(dict(row))
    return described


def describe_monitor(
    monitor_payload: Mapping | None,
    trading_date: str | None,
    *,
    settings,
    stats_dir: str | os.PathLike[str] | None,
    now_ms: int,
) -> dict | None:
    """The live (or last persisted) monitor's day, described like a history row."""
    if not monitor_payload or not trading_date:
        return None
    metrics = monitor_payload.get("global") or {}
    summary = {"trading_date": trading_date, **{k: metrics.get(k) for k in SUMMARY_KEYS}}
    return describe_rows([summary], settings=settings, stats_dir=stats_dir, now_ms=now_ms)[0]


def incident_report(
    stats_dir: str | os.PathLike[str] | None,
    trading_date: str,
    *,
    settings,
    now_ms: int,
) -> dict:
    """The raw ledger for a day plus its timeline analysis (counts, outages, causes)."""
    ledger = _ledger(stats_dir, trading_date)
    window = safe_session_window(settings, trading_date)
    windows = [(s, e) for s, e in (window or {}).get("windows") or ()]
    return {
        "trading_date": trading_date,
        "recorded": ledger is not None,
        "session_window": window,
        "entries": (ledger or {}).get("entries") or [],
        "counts": (ledger or {}).get("counts") or {},
        "analysis": analyze_incidents(ledger, windows, now_ms) if ledger else None,
    }


__all__ = [
    "SUMMARY_KEYS",
    "describe_monitor",
    "describe_rows",
    "incident_report",
    "safe_session_window",
    "session_window_for",
]
