"""Market calendar API: operator holidays, special sessions, and an on-demand token fetch.

Reads are open like the rest of the status API. Every write — and the token fetch, which
reaches the broker login service — needs the operator credential in
``X-Release-Maintenance-Token``, the same credential the manual login routes use.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.auth import OPERATOR_TOKEN_HEADER, _authorize
from app.ops.automation import day_windows
from app.ops.market_overrides import (
    MAX_CAPTURE_LEAD_MINUTES,
    MAX_NOTE_LENGTH,
    Holiday,
    MarketOverrides,
    OverrideValidationError,
    SpecialSession,
    installed_store,
)

logger = logging.getLogger(__name__)

DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"
HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

OperatorToken = Annotated[
    str | None, Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256)
]
DatePath = Annotated[str, Path(pattern=DATE_PATTERN)]


class HolidayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(pattern=DATE_PATTERN)
    note: str = Field(default="", max_length=MAX_NOTE_LENGTH)


class SpecialSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(pattern=DATE_PATTERN)
    open: str = Field(pattern=HHMM_PATTERN)
    close: str = Field(pattern=HHMM_PATTERN)
    token_fetch_at: str | None = Field(default=None, pattern=HHMM_PATTERN)
    capture_lead_minutes: int = Field(default=5, ge=0, le=MAX_CAPTURE_LEAD_MINUTES)
    note: str = Field(default="", max_length=MAX_NOTE_LENGTH)


def _service(request: Request):
    service = getattr(request.app.state, "session_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="backend not configured")
    return service


def _store():
    store = installed_store()
    if store is None:
        raise HTTPException(status_code=503, detail="market calendar store not configured")
    return store


def _require_not_past(service, day: str) -> None:
    today = service.trading_date()
    try:
        parsed = date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{day} is not a real date") from exc
    if parsed.isoformat() < today:
        raise HTTPException(
            status_code=422, detail=f"{day} is in the past (today is {today})"
        )


def _today_view(request: Request, service, overrides: MarketOverrides) -> dict:
    now = service._clock()
    calendar = service.calendar
    settings = service.settings
    special = overrides.special_for(service.trading_date())
    automation = getattr(request.app.state, "daily_automation", None)
    capture_calendar = automation.calendar if automation is not None else calendar
    windows = day_windows(
        capture_calendar, now, settings.auth_poll_start, settings.auth_poll_end
    )
    session = service.active_session()
    controller = getattr(request.app.state, "capture_controller", None)
    return {
        "date": service.trading_date(),
        "local_time": calendar.local_dt(now).strftime("%H:%M:%S"),
        "is_trading_day": calendar.is_trading_day(now),
        "market_phase": calendar.phase(now),
        "special_session": special.__dict__ if special is not None else None,
        "token_window": [f"{windows.auth_start:%H:%M}", f"{windows.auth_end:%H:%M}"],
        "capture_window": [
            f"{windows.capture_start:%H:%M}",
            f"{windows.capture_end:%H:%M}",
        ],
        "token_ready": bool(session and session.access_token),
        "token_fetched_at": session.access_token_at if session else None,
        "capture_running": bool(controller is not None and controller.running),
        "automation": automation.status() if automation is not None else None,
    }


def _payload(request: Request, service, overrides: MarketOverrides) -> dict:
    settings = service.settings
    return {
        **overrides.to_dict(),
        "env_holidays": sorted(getattr(settings, "market_holidays", []) or []),
        "load_errors": list(getattr(installed_store(), "errors", ()) or ()),
        "regular_hours": {
            "market_open": settings.market_open,
            "market_close": settings.market_close,
            "capture_window": list(settings.capture_window),
            "token_window": [settings.auth_poll_start, settings.auth_poll_end],
        },
        "today": _today_view(request, service, overrides),
    }


def _save(store, update) -> MarketOverrides:
    try:
        return store.save(update(store.reload()))
    except OverrideValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("market calendar could not be saved (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="the calendar file could not be written on the server"
        ) from exc


def create_calendar_router() -> APIRouter:
    router = APIRouter(prefix="/api/calendar", tags=["calendar"])

    @router.get("")
    async def get_calendar(request: Request) -> dict:
        service = _service(request)
        return _payload(request, service, _store().load())

    @router.post("/holidays")
    async def add_holiday(
        request: Request, body: HolidayRequest, operator_token: OperatorToken = None
    ) -> dict:
        service = _service(request)
        _authorize(service, operator_token)
        _require_not_past(service, body.date)
        try:
            holiday = Holiday.create(body.date, body.note)
        except OverrideValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = _save(_store(), lambda current: current.with_holiday(holiday))
        logger.info("market calendar: holiday added for %s", holiday.date)
        return _payload(request, service, saved)

    @router.delete("/holidays/{day}")
    async def remove_holiday(
        request: Request, day: DatePath, operator_token: OperatorToken = None
    ) -> dict:
        service = _service(request)
        _authorize(service, operator_token)
        saved = _save(_store(), lambda current: current.without_holiday(day))
        logger.info("market calendar: holiday removed for %s", day)
        return _payload(request, service, saved)

    @router.put("/special-sessions")
    async def upsert_special_session(
        request: Request,
        body: SpecialSessionRequest,
        operator_token: OperatorToken = None,
    ) -> dict:
        service = _service(request)
        _authorize(service, operator_token)
        _require_not_past(service, body.date)
        try:
            session = SpecialSession.create(
                body.date,
                body.open,
                body.close,
                body.token_fetch_at,
                body.capture_lead_minutes,
                body.note,
            )
        except OverrideValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        saved = _save(_store(), lambda current: current.with_special(session))
        logger.info(
            "market calendar: special session %s %s-%s (token from %s, capture lead %d min)",
            session.date,
            session.open,
            session.close,
            session.token_fetch_at,
            session.capture_lead_minutes,
        )
        return _payload(request, service, saved)

    @router.delete("/special-sessions/{day}")
    async def remove_special_session(
        request: Request, day: DatePath, operator_token: OperatorToken = None
    ) -> dict:
        service = _service(request)
        _authorize(service, operator_token)
        saved = _save(_store(), lambda current: current.without_special(day))
        logger.info("market calendar: special session removed for %s", day)
        return _payload(request, service, saved)

    @router.post("/fetch-token")
    async def fetch_token(request: Request, operator_token: OperatorToken = None) -> dict:
        """Fetch and validate today's broker token now, instead of waiting for the window."""
        service = _service(request)
        _authorize(service, operator_token)
        try:
            session = await run_in_threadpool(service.acquire_broker_session)
        except Exception as exc:  # noqa: BLE001 - report, never leak details
            logger.warning("manual token fetch failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=502,
                detail=(
                    "the broker returned a token that Kite rejected or could not be "
                    "reached; check the broker login service"
                ),
            ) from exc
        if session is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "the broker login service has no authenticated token for today yet; "
                    "log in there, then fetch again"
                ),
            )
        logger.info("manual token fetch succeeded for %s", session.trading_date)
        return _payload(request, service, _store().load())

    return router
