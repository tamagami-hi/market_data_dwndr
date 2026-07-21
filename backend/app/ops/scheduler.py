"""Market-hours scheduler: turns session-phase transitions into capture events.

Pure transition logic (testable) plus a thin async loop. Entering ``OPEN`` starts
capture; leaving ``OPEN`` (to CLOSED/HOLIDAY) stops capture and triggers the EOD sweep
(docs/60-operations/operations-runbook.md).
"""

from __future__ import annotations

import asyncio
import logging

from app.ops.calendar import PHASE_OPEN, TradingCalendar
from app.session import now_ms

logger = logging.getLogger(__name__)

EVENT_START_CAPTURE = "START_CAPTURE"
EVENT_STOP_CAPTURE = "STOP_CAPTURE"
EVENT_RUN_EOD = "RUN_EOD"


class PhaseMachine:
    """Emits capture events on phase transitions. Idempotent per phase."""

    def __init__(self) -> None:
        self.current: str | None = None

    def update(self, phase: str) -> list[str]:
        """Feed the latest phase; return the events triggered by the transition."""
        if phase == self.current:
            return []
        prev = self.current
        self.current = phase
        events: list[str] = []
        if phase == PHASE_OPEN:
            events.append(EVENT_START_CAPTURE)
        elif prev == PHASE_OPEN:
            # leaving the session (OPEN -> CLOSED/HOLIDAY/PRE_OPEN of next day)
            events.append(EVENT_STOP_CAPTURE)
            events.append(EVENT_RUN_EOD)
        return events


class CaptureScheduler:
    """Polls the calendar and drives start/stop/EOD callbacks."""

    def __init__(
        self,
        calendar: TradingCalendar,
        on_start_capture,
        on_stop_capture,
        on_run_eod,
        *,
        clock=now_ms,
        poll_interval_s: float = 5.0,
    ) -> None:
        self.calendar = calendar
        self.on_start_capture = on_start_capture
        self.on_stop_capture = on_stop_capture
        self.on_run_eod = on_run_eod
        self._clock = clock
        self.poll_interval_s = poll_interval_s
        self.machine = PhaseMachine()

    def tick(self, now_ms_value: int | None = None) -> list[str]:
        """One scheduler step: evaluate phase and dispatch any transition events."""
        now = now_ms_value if now_ms_value is not None else self._clock()
        events = self.machine.update(self.calendar.phase(now))
        for event in events:
            self._dispatch(event)
        return events

    def _dispatch(self, event: str) -> None:
        if event == EVENT_START_CAPTURE:
            self.on_start_capture()
        elif event == EVENT_STOP_CAPTURE:
            self.on_stop_capture()
        elif event == EVENT_RUN_EOD:
            self.on_run_eod()

    async def run(self, stop_event: asyncio.Event) -> None:  # pragma: no cover - live loop
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("scheduler tick failed")
            await asyncio.sleep(self.poll_interval_s)
