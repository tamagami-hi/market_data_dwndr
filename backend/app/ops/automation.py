"""Pure daily broker/capture/EOD decision policy."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, time
from pathlib import Path

from app.ops.calendar import TradingCalendar
from app.session import now_ms
from app.ws import protocol

logger = logging.getLogger(__name__)

ACTION_FETCH_BROKER_TOKEN = "FETCH_BROKER_TOKEN"
ACTION_START_CAPTURE = "START_CAPTURE"
ACTION_STOP_CAPTURE = "STOP_CAPTURE"
ACTION_RUN_EOD = "RUN_EOD"

# How often a *persisting* action failure is re-logged (with a running count) instead of
# emitting an identical line on every poll.
FAILURE_SUMMARY_INTERVAL_S = 300


@dataclass(frozen=True)
class AutomationState:
    last_broker_poll_at_ms: int | None = None
    eod_completed_date: str | None = None
    eod_in_progress_date: str | None = None


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _plus_seconds(moment: time, seconds: int) -> time:
    """``moment`` shifted forward, clamped to 23:59:59 rather than wrapping past midnight.

    The tail is minutes, not hours, so clamping is the safe failure mode: an EOD that
    slips to 23:59:59 is visibly late, whereas a wrap to 00:0x would fire *before* the
    close on the same calendar day and finalize the archive mid-session.
    """
    total = moment.hour * 3600 + moment.minute * 60 + moment.second + max(0, int(seconds))
    if total >= 24 * 3600:
        return time(23, 59, 59)
    return time(total // 3600, (total % 3600) // 60, total % 60)


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
    eod_tail_seconds: int = 0,
) -> tuple[AutomationState, tuple[str, ...]]:
    """Return a new policy state and ordered side-effect actions for one tick.

    ``calendar.market_open``/``market_close`` here are the **capture window** (when the
    process runs), not a market session. The window ends at the equity-derivatives close,
    so at that instant the final derivative frames are still being flushed:
    ``eod_tail_seconds`` is a deliberate drain allowance before the archive is finalized.
    It is explicitly *not* an extension of any market session — nothing is owed in the tail.
    """
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
        eod_at = _plus_seconds(calendar.market_close, eod_tail_seconds)
        # Compression REMOVES the raw .bin and moves it to the archive. Doing that while the
        # last derivative frames are still in a writer queue would destroy them, so the
        # sweep waits for the drain tail AND for capture to have actually stopped.
        run_eod = needs_eod and local_time >= eod_at and not is_capture_running
        # STOP_CAPTURE is emitted while capture runs, and once more immediately before the
        # sweep as a guaranteed final flush — not on every idle tick in between.
        if is_capture_running or run_eod:
            actions = (*actions, ACTION_STOP_CAPTURE)
        if run_eod:
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
        hub=None,
        eod_fn=None,
        clock=now_ms,
        tick_interval_s: float = 5.0,
    ) -> None:
        self.settings = settings
        self.session_service = session_service
        self.capture_controller = capture_controller
        self.hub = hub
        # The capture WINDOW (when the process runs), not the market session (when a frame
        # is owed). Starting earlier gets the socket and subscriptions up before the first
        # print; sessions decide what counts as data.
        capture_start, capture_end = (
            settings.capture_window
            if hasattr(settings, "capture_window")
            else (settings.market_open, settings.market_close)
        )
        self.calendar = TradingCalendar(
            holidays=set(getattr(settings, "market_holidays", [])),
            timezone_name=settings.timezone,
            market_open=capture_start,
            market_close=capture_end,
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
        self._last_compression: dict | None = None
        self._last_reconciliation: dict | None = None
        self._last_tick_at_ms: int | None = None
        # Repeat-failure suppression, so a condition that persists for an hour is logged
        # as a duration rather than once per poll.
        self._failure_signature: tuple[str, str] | None = None
        self._failure_repeats = 0
        self._failure_first_ms: int | None = None
        self._failure_logged_ms: int | None = None
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
                eod_tail_seconds=getattr(self.settings, "eod_tail_seconds", 0),
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
                loop = asyncio.get_running_loop()
                # Archive-truth completeness FIRST: compression removes the raw ``.bin``,
                # and this is the one loss figure not derived from process telemetry.
                await asyncio.to_thread(self._reconcile_session_from_archive)

                def _on_progress(state: dict) -> None:
                    # Called from the compression worker thread; hand the
                    # broadcast back to the event loop without blocking the sweep.
                    self._last_compression = state
                    if self.hub is not None:
                        loop.call_soon_threadsafe(
                            lambda s=state: loop.create_task(
                                self.hub.broadcast(
                                    "capture-status", protocol.compression_progress(s)
                                )
                            )
                        )

                result = await asyncio.to_thread(
                    self._eod_fn,
                    self.settings.market_data_path,
                    self.settings.archive_data_path,
                    level=self.settings.zstd_level,
                    threads=getattr(self.settings, "zstd_threads", 0),
                    progress_cb=_on_progress,
                )
                completed_date = self._state.eod_in_progress_date
                # Persist a cross-day compression summary for the dashboard.
                if result is not None:
                    try:
                        from app.ops import stats_store

                        stats_root = getattr(self.settings, "stats_dir", None)
                        if stats_root is not None:
                            stats_store.record_compression(
                                stats_root,
                                result,
                                trading_date=completed_date or "unknown",
                                threads=getattr(self.settings, "zstd_threads", None),
                            )
                    except Exception:  # noqa: BLE001 - persistence must not fail EOD
                        logger.debug("failed to persist compression stats", exc_info=True)
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
                failed = {**(self._last_compression or {}), "phase": "failed"}
                self._last_compression = failed
                if self.hub is not None:
                    try:
                        await self.hub.broadcast(
                            "capture-status", protocol.compression_progress(failed)
                        )
                    except Exception:  # noqa: BLE001 - never mask the original failure
                        pass
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
            self._log_action_failure(action, exc)
            return False

    def _reconcile_session_from_archive(self) -> dict | None:
        """Reconcile the day's owed grid against the frames actually on disk.

        Runs at EOD, before compression removes the raw ``.bin``. This is the only
        completeness figure in the system that survives a crash, a container kill or a
        reboot, because both of its inputs do: the schedule comes from configuration plus
        the trading date, and the frame timestamps come from the archive. Every live
        percentage is bounded by what the process observed and therefore cannot see a
        session it was absent for.
        """
        try:
            from app.ops import stats_store
            from app.ops.reconcile_session import reconcile_session
            from app.ops.sessions import (
                SESSION_EQUITY_DERIV,
                build_session_registry,
            )

            trading_date = self._state.eod_in_progress_date or self.calendar.trading_date(
                self._clock()
            )
            day = date.fromisoformat(trading_date)
            settings = self.settings

            artifact_paths: dict[str, Path] = {
                name: settings.indices_dir / name / f"{trading_date}.bin"
                for name in getattr(settings, "indices", ())
            }
            artifact_paths["STOCKS"] = settings.stocks_dir / f"{trading_date}.bin"
            if getattr(settings, "indices_fno_enabled", True):
                artifact_paths["INDICES_FnO"] = (
                    settings.indices_fno_dir / f"{trading_date}.bin"
                )
            # Only artifacts that produced something are reconciled; a domain that was
            # never configured for the day is not a hole in it.
            present = {
                name: path
                for name, path in artifact_paths.items()
                if any(p.exists() for p in [path, *path.parent.glob(f"{path.stem}.p*.bin")])
            }
            if not present:
                logger.info("no capture files for %s to reconcile", trading_date)
                return None

            registry = build_session_registry(
                settings, {name: SESSION_EQUITY_DERIV for name in present}
            )
            # Per-artifact grids, not one shared window. Every artifact TickVault writes
            # carries a derivative leg trading to 15:40, so each is reconciled against the
            # derivatives grid — but the mapping is now explicit and recorded, so an
            # artifact moved to the cash session is measured against 09:15-15:15
            # automatically instead of reporting a normal cash close as 25 minutes of loss.
            windows = {name: registry.scheduled_windows_on(name, day) for name in present}
            auctions = {name: registry.auction_windows_on(name, day) for name in present}
            record = reconcile_session(
                present,
                windows,
                trading_date=trading_date,
                auction_windows=auctions,
            )
            self._last_reconciliation = record

            stats_root = getattr(settings, "stats_dir", None)
            if stats_root is not None:
                stats_store.record_reconciliation(stats_root, record)
            return record
        except Exception:  # noqa: BLE001 - reconciliation must never block the EOD sweep
            logger.warning("archive reconciliation failed", exc_info=True)
            return None

    def _log_action_failure(self, action: str, exc: BaseException) -> None:
        """Log a failed action once per distinct cause, not once per poll.

        A broker session that Kite refuses can persist for over an hour. Emitting the same
        WARNING every 60 seconds buried the signal (98 identical lines on 2026-08-28) and
        never escalated. Repeats are summarised at a fixed interval with a running count,
        so the log shows the *duration* of the condition instead of its cadence.
        """
        signature = (action, type(exc).__name__)
        now = self._clock()
        if signature != self._failure_signature:
            self._failure_signature = signature
            self._failure_repeats = 0
            self._failure_first_ms = now
            self._failure_logged_ms = now
            logger.warning(
                "daily automation action failed: %s (%s)", action, type(exc).__name__
            )
            return
        self._failure_repeats += 1
        since_ms = now - (self._failure_logged_ms or now)
        if since_ms < FAILURE_SUMMARY_INTERVAL_S * 1_000:
            logger.debug(
                "daily automation action still failing: %s (%s)", action, type(exc).__name__
            )
            return
        self._failure_logged_ms = now
        elapsed_s = max(0, (now - (self._failure_first_ms or now)) // 1000)
        logger.error(
            "daily automation action %s has been failing for %ds (%s, %d attempts). "
            "This is not self-healing: check the broker session.",
            action,
            elapsed_s,
            type(exc).__name__,
            self._failure_repeats + 1,
        )

    def status(self) -> dict:
        phase = "waiting"
        eod_at = _plus_seconds(
            self.calendar.market_close, getattr(self.settings, "eod_tail_seconds", 0)
        )
        if self._last_tick_at_ms is not None:
            local_time = self.calendar.local_dt(self._last_tick_at_ms).time().replace(tzinfo=None)
            if _parse_hhmm(self.settings.auth_poll_start) <= local_time < _parse_hhmm(
                self.settings.auth_poll_end
            ):
                phase = "auth_window"
            elif self.calendar.market_open <= local_time < self.calendar.market_close:
                phase = "capture_window"
            elif local_time >= self.calendar.market_close:
                # Between the capture end and the EOD sweep the writers are draining the
                # final derivative frames. Reported as its own phase so an operator can
                # tell "waiting to finalize safely" from "compression running".
                phase = "eod" if local_time >= eod_at else "draining"
        return {
            "phase": phase,
            "eod_at": eod_at.strftime("%H:%M:%S"),
            "eod_tail_seconds": int(getattr(self.settings, "eod_tail_seconds", 0)),
            "last_action": self._last_action,
            "last_error": self._last_error,
            "last_tick_at": self._last_tick_at_ms,
            "last_broker_poll_at": self._state.last_broker_poll_at_ms,
            "eod_completed_date": self._state.eod_completed_date,
            "eod_in_progress_date": self._state.eod_in_progress_date,
            "compression": self._last_compression,
            # Archive-derived completeness for the last reconciled trading date. The one
            # loss figure that is not bounded by what this process happened to observe.
            "reconciliation": self._last_reconciliation,
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
