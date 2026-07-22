"""Pure daily broker/capture/EOD decision policy."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import time

from app.ops.calendar import TradingCalendar
from app.session import now_ms

logger = logging.getLogger(__name__)

ACTION_FETCH_BROKER_TOKEN = "FETCH_BROKER_TOKEN"
ACTION_START_CAPTURE = "START_CAPTURE"
ACTION_STOP_CAPTURE = "STOP_CAPTURE"
ACTION_RUN_EOD = "RUN_EOD"


@dataclass(frozen=True)
class AutomationState:
    last_broker_poll_at_ms: int | None = None
    eod_completed_date: str | None = None
    eod_in_progress_date: str | None = None


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def decide_automation(
    now_ms: int,
    state: AutomationState,
    *,
    calendar,
    auth_poll_start: str,
    auth_poll_end: str,
    has_valid_session: bool,
    is_capture_ready: bool,
    is_capture_running: bool,
    broker_retry_interval_s: int = 60,
) -> tuple[AutomationState, tuple[str, ...]]:
    """Return a new policy state and ordered side-effect actions for one tick."""
    local_dt = calendar.local_dt(now_ms)
    trading_date = local_dt.strftime("%Y-%m-%d")
    local_time = local_dt.time().replace(tzinfo=None)
    actions: tuple[str, ...] = ()
    next_state = state

    if not calendar.is_trading_day(now_ms):
        if is_capture_running:
            actions = (ACTION_STOP_CAPTURE,)
        return next_state, actions

    poll_start = _parse_hhmm(auth_poll_start)
    poll_end = _parse_hhmm(auth_poll_end)
    is_auth_window = poll_start <= local_time < poll_end
    is_capture_window = calendar.market_open <= local_time < calendar.market_close
    if (is_auth_window or is_capture_window) and not has_valid_session:
        interval_ms = broker_retry_interval_s * 1_000
        is_due = (
            state.last_broker_poll_at_ms is None
            or now_ms - state.last_broker_poll_at_ms >= interval_ms
        )
        if is_due:
            next_state = replace(next_state, last_broker_poll_at_ms=now_ms)
            actions = (*actions, ACTION_FETCH_BROKER_TOKEN)

    if is_capture_window:
        if is_capture_ready and not is_capture_running:
            actions = (*actions, ACTION_START_CAPTURE)
        return next_state, actions

    if local_time >= calendar.market_close:
        needs_eod = (
            state.eod_completed_date != trading_date
            and state.eod_in_progress_date != trading_date
        )
        if is_capture_running or needs_eod:
            actions = (*actions, ACTION_STOP_CAPTURE)
        if needs_eod:
            actions = (*actions, ACTION_RUN_EOD)
            next_state = replace(next_state, eod_in_progress_date=trading_date)
        return next_state, actions

    if is_capture_running:
        actions = (*actions, ACTION_STOP_CAPTURE)
    return next_state, actions


class DailyAutomationService:
    """Dispatch the pure policy through broker, capture, and EOD adapters."""

    def __init__(
        self,
        settings,
        session_service,
        capture_controller,
        *,
        eod_fn=None,
        clock=now_ms,
        tick_interval_s: float = 5.0,
    ) -> None:
        self.settings = settings
        self.session_service = session_service
        self.capture_controller = capture_controller
        self.calendar = TradingCalendar(
            timezone_name=settings.timezone,
            market_open=settings.market_open,
            market_close=settings.market_close,
        )
        if eod_fn is None:
            from app.ops.eod import compress_raw_files

            eod_fn = compress_raw_files
        self._eod_fn = eod_fn
        self._clock = clock
        self._tick_interval_s = tick_interval_s
        self._state = AutomationState()
        self._last_action: str | None = None
        self._last_error: str | None = None
        self._last_tick_at_ms: int | None = None
        self._lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None

    async def tick(self, now_ms_value: int | None = None) -> tuple[str, ...]:
        async with self._lock:
            current_time = now_ms_value if now_ms_value is not None else self._clock()
            session = self.session_service.active_session()
            has_session = bool(session and session.access_token)
            is_capture_ready = bool(session and session.capture_ready)
            next_state, actions = decide_automation(
                current_time,
                self._state,
                calendar=self.calendar,
                auth_poll_start=self.settings.auth_poll_start,
                auth_poll_end=self.settings.auth_poll_end,
                has_valid_session=has_session,
                is_capture_ready=is_capture_ready,
                is_capture_running=self.capture_controller.running,
                broker_retry_interval_s=self.settings.auth_poll_interval_seconds,
            )
            self._state = next_state
            self._last_tick_at_ms = current_time
            for action in actions:
                if not await self._dispatch(action):
                    if action == ACTION_STOP_CAPTURE:
                        self._state = replace(
                            self._state,
                            eod_completed_date=None,
                            eod_in_progress_date=None,
                        )
                    break
            return actions

    async def _dispatch(self, action: str) -> bool:
        self._last_action = action
        self._last_error = None
        try:
            if action == ACTION_FETCH_BROKER_TOKEN:
                session = await asyncio.to_thread(
                    self.session_service.acquire_broker_session
                )
                if session is None:
                    self._last_error = (
                        "shared token is not ready; retrying in the auth window"
                    )
            elif action == ACTION_START_CAPTURE:
                await self.capture_controller.start()
            elif action == ACTION_STOP_CAPTURE:
                await self.capture_controller.stop()
            elif action == ACTION_RUN_EOD:
                await asyncio.to_thread(
                    self._eod_fn,
                    self.settings.market_data_path,
                    self.settings.archive_data_path,
                    level=self.settings.zstd_level,
                )
                completed_date = self._state.eod_in_progress_date
                self._state = replace(
                    self._state,
                    eod_completed_date=completed_date,
                    eod_in_progress_date=None,
                )
            return True
        except Exception as exc:  # noqa: BLE001 - keep scheduler alive; redact details
            if action == ACTION_FETCH_BROKER_TOKEN:
                self._last_error = "shared token is not ready; retrying in the auth window"
            elif action == ACTION_RUN_EOD:
                self._last_error = "end-of-day compression failed; raw files were retained"
                self._state = replace(
                    self._state,
                    eod_completed_date=None,
                    eod_in_progress_date=None,
                )
            elif action == ACTION_STOP_CAPTURE:
                self._last_error = (
                    "end-of-day compression blocked because capture did not flush safely"
                )
            else:
                self._last_error = "capture automation action failed; inspect backend logs"
            logger.warning(
                "daily automation action failed: %s (%s)", action, type(exc).__name__
            )
            return False

    def status(self) -> dict:
        phase = "waiting"
        if self._last_tick_at_ms is not None:
            local_time = self.calendar.local_dt(self._last_tick_at_ms).time().replace(tzinfo=None)
            if _parse_hhmm(self.settings.auth_poll_start) <= local_time < _parse_hhmm(
                self.settings.auth_poll_end
            ):
                phase = "auth_window"
            elif self.calendar.market_open <= local_time < self.calendar.market_close:
                phase = "capture_window"
            elif local_time >= self.calendar.market_close:
                phase = "eod"
        return {
            "phase": phase,
            "last_action": self._last_action,
            "last_error": self._last_error,
            "last_tick_at": self._last_tick_at_ms,
            "last_broker_poll_at": self._state.last_broker_poll_at_ms,
            "eod_completed_date": self._state.eod_completed_date,
            "eod_in_progress_date": self._state.eod_in_progress_date,
        }

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self.run(self._stop_event))

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            await self._task
        self._task = None
        self._stop_event = None

    async def run(self, stop_event: asyncio.Event) -> None:  # pragma: no cover - live loop
        while not stop_event.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - keep the daily service alive
                self._last_error = "daily automation tick failed; retrying"
                logger.warning(
                    "daily automation tick failed (%s)", type(exc).__name__
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._tick_interval_s)
            except TimeoutError:
                continue
