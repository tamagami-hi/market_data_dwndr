"""Daily broker/capture/EOD policy and risk-free-rate freshness tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.api.capture import CaptureController
from app.ops.automation import (
    ACTION_FETCH_BROKER_TOKEN,
    ACTION_RUN_EOD,
    ACTION_START_CAPTURE,
    ACTION_STOP_CAPTURE,
    AutomationState,
    DailyAutomationService,
    decide_automation,
)
from app.ops.calendar import IST_FALLBACK, TradingCalendar
from app.session import SessionState, load_session, resolve_risk_free_rate, save_session
from app.session_service import SessionService


def _ms(day: int, hour: int, minute: int, second: int = 0) -> int:
    return int(
        datetime(2026, 7, day, hour, minute, second, tzinfo=IST_FALLBACK).timestamp() * 1000
    )


def _decision(
    now: int,
    state: AutomationState,
    *,
    has_session: bool = False,
    is_capture_ready: bool = False,
    is_capture_running: bool = False,
):
    return decide_automation(
        now,
        state,
        calendar=TradingCalendar(market_open="09:00", market_close="15:30"),
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        has_valid_session=has_session,
        is_capture_ready=is_capture_ready,
        is_capture_running=is_capture_running,
        broker_retry_interval_s=60,
    )


def test_broker_polling_is_strictly_windowed_and_rate_limited():
    state = AutomationState()
    state, actions = _decision(_ms(21, 8, 29, 59), state)
    assert actions == ()

    state, actions = _decision(_ms(21, 8, 30), state)
    assert actions == (ACTION_FETCH_BROKER_TOKEN,)

    state, actions = _decision(_ms(21, 8, 30, 59), state)
    assert actions == ()
    state, actions = _decision(_ms(21, 8, 31), state)
    assert actions == (ACTION_FETCH_BROKER_TOKEN,)

    state, actions = _decision(_ms(21, 8, 45), state, has_session=True)
    assert actions == ()
    state, actions = _decision(_ms(21, 9, 0), state)
    assert actions == (ACTION_FETCH_BROKER_TOKEN,)

    state, actions = _decision(_ms(21, 9, 0, 30), state)
    assert actions == ()
    state, actions = _decision(_ms(21, 9, 1), state)
    assert actions == (ACTION_FETCH_BROKER_TOKEN,)
    state, actions = _decision(_ms(21, 10, 0), state, has_session=True)
    assert actions == ()


def test_capture_window_starts_ready_session_and_stops_before_eod():
    state = AutomationState()
    state, actions = _decision(
        _ms(21, 9, 0), state, has_session=True, is_capture_ready=True
    )
    assert actions == (ACTION_START_CAPTURE,)

    state, actions = _decision(
        _ms(21, 15, 29, 59),
        state,
        has_session=True,
        is_capture_ready=True,
        is_capture_running=True,
    )
    assert actions == ()

    state, actions = _decision(_ms(21, 15, 30), state, is_capture_running=True)
    assert actions == (ACTION_STOP_CAPTURE, ACTION_RUN_EOD)


def test_startup_after_close_repairs_eod_once_per_day():
    state, actions = _decision(_ms(21, 16, 0), AutomationState())
    assert actions == (ACTION_STOP_CAPTURE, ACTION_RUN_EOD)

    state, actions = _decision(_ms(21, 16, 5), state)
    assert actions == ()


def test_rate_is_reused_tomorrow_and_requires_update_on_third_trading_day(tmp_path):
    save_session(
        tmp_path,
        SessionState(
            "2026-07-20",
            "OLD_TOKEN",
            0.065,
            1,
            1,
            risk_free_rate_as_of="2026-07-20",
        ),
    )

    tomorrow = resolve_risk_free_rate(tmp_path, "2026-07-21")
    assert tomorrow.risk_free_rate == 0.065
    assert tomorrow.risk_free_rate_as_of == "2026-07-20"
    assert tomorrow.rate_update_required is False
    assert tomorrow.can_capture is True

    third_day = resolve_risk_free_rate(tmp_path, "2026-07-22")
    assert third_day.risk_free_rate == 0.065
    assert third_day.rate_update_required is True
    assert third_day.can_capture is False


def test_yield_freshness_counts_trading_days_and_skips_weekends(tmp_path):
    save_session(
        tmp_path,
        SessionState(
            "2026-07-24",
            "FRIDAY_TOKEN",
            0.065,
            1,
            1,
            risk_free_rate_as_of="2026-07-24",
        ),
    )

    monday = resolve_risk_free_rate(tmp_path, "2026-07-27")
    tuesday = resolve_risk_free_rate(tmp_path, "2026-07-28")

    assert monday.rate_update_required is False
    assert monday.can_capture is True
    assert tuesday.rate_update_required is True
    assert tuesday.can_capture is False


def test_weekends_never_poll_token_start_capture_or_run_eod():
    state = AutomationState()

    for hour, minute in ((8, 30), (9, 0), (15, 30), (23, 0)):
        state, actions = _decision(_ms(25, hour, minute), state)
        assert actions == ()


def test_legacy_session_defaults_rate_provenance_to_its_trading_date():
    state = SessionState.from_dict(
        {
            "trading_date": "2026-07-20",
            "access_token": "TOKEN",
            "risk_free_rate": 0.065,
            "access_token_at": 1,
            "started_at": 1,
        }
    )

    assert state.risk_free_rate_as_of == "2026-07-20"
    assert state.rate_update_required is False
    assert state.capture_ready is True


@pytest.mark.parametrize("risk_free_rate", [float("nan"), float("inf"), -0.01])
def test_session_state_rejects_invalid_persisted_yield(risk_free_rate):
    with pytest.raises(ValueError, match="yield"):
        SessionState.from_dict(
            {
                "trading_date": "2026-07-20",
                "access_token": "TOKEN",
                "risk_free_rate": risk_free_rate,
                "access_token_at": 1,
                "started_at": 1,
            }
        )


def _settings(tmp_path):
    return SimpleNamespace(
        market_holidays=[],
        state_dir=tmp_path,
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        kite_api_key="key",
        kite_api_secret="secret",
        kite_user_id="AB1234",
        kite_password=None,
        kite_static_ip=None,
        kite_http_proxy=None,
        kite_token_broker_url="https://calspread.online/api/kite/token",
        kite_token_broker_passcode=object(),
        risk_free_rate=None,
    )


def test_rejected_previous_token_falls_back_to_broker_and_reuses_rate(tmp_path):
    save_session(
        tmp_path,
        SessionState(
            "2026-07-20", "OLD", 0.065, 1, 1, risk_free_rate_as_of="2026-07-20"
        ),
    )
    validated: list[str] = []

    def validate(token: str) -> None:
        validated.append(token)
        if token == "OLD":
            raise ValueError("expired previous token")

    service = SessionService(
        _settings(tmp_path),
        clock=lambda: _ms(21, 8, 30),
        broker_fetcher=lambda: "NEW_TOKEN",
        broker_validator=validate,
    )

    state = service.acquire_broker_session()

    assert state is not None
    assert validated == ["OLD", "NEW_TOKEN"]
    assert state.access_token == "NEW_TOKEN"
    assert state.risk_free_rate == 0.065
    assert state.risk_free_rate_as_of == "2026-07-20"
    assert state.capture_ready is True
    assert load_session(tmp_path, "2026-07-21") == state


def test_valid_previous_token_is_reused_before_polling_broker(tmp_path):
    save_session(
        tmp_path,
        SessionState(
            "2026-07-20",
            "STILL_VALID",
            0.065,
            1,
            1,
            risk_free_rate_as_of="2026-07-20",
        ),
    )
    validated: list[str] = []
    service = SessionService(
        _settings(tmp_path),
        clock=lambda: _ms(21, 10, 0),
        broker_fetcher=lambda: (_ for _ in ()).throw(
            AssertionError("broker must not be polled when prior token is valid")
        ),
        broker_validator=validated.append,
    )

    state = service.acquire_broker_session()

    assert state is not None
    assert state.access_token == "STILL_VALID"
    assert validated == ["STILL_VALID"]


def test_rejected_previous_token_is_attempted_only_once_per_process(tmp_path):
    save_session(
        tmp_path,
        SessionState("2026-07-20", "EXPIRED", 0.065, 1, 1),
    )
    validated: list[str] = []

    def reject(token: str) -> None:
        validated.append(token)
        raise ValueError("invalid token")

    service = SessionService(
        _settings(tmp_path),
        clock=lambda: _ms(21, 10, 0),
        broker_fetcher=lambda: None,
        broker_validator=reject,
    )

    assert service.acquire_broker_session() is None
    assert service.acquire_broker_session() is None
    assert validated == ["EXPIRED"]


def test_invalid_broker_token_is_never_persisted(tmp_path):
    def reject(_token: str) -> None:
        raise ValueError("invalid token")

    service = SessionService(
        _settings(tmp_path),
        clock=lambda: _ms(21, 8, 30),
        broker_fetcher=lambda: "BAD_TOKEN",
        broker_validator=reject,
    )

    with pytest.raises(ValueError, match="invalid token"):
        service.acquire_broker_session()
    assert load_session(tmp_path, "2026-07-21") is None


def test_third_day_rate_update_makes_pending_broker_session_capture_ready(tmp_path):
    save_session(
        tmp_path,
        SessionState(
            "2026-07-20", "OLD", 0.065, 1, 1, risk_free_rate_as_of="2026-07-20"
        ),
    )
    service = SessionService(
        _settings(tmp_path),
        clock=lambda: _ms(22, 8, 30),
        broker_fetcher=lambda: "NEW_TOKEN",
        broker_validator=lambda _token: None,
    )
    pending = service.acquire_broker_session()
    assert pending is not None
    assert pending.rate_update_required is True
    assert pending.capture_ready is False

    updated = service.update_risk_free_rate(0.066)

    assert updated.risk_free_rate == 0.066
    assert updated.risk_free_rate_as_of == "2026-07-22"
    assert updated.rate_update_required is False
    assert updated.capture_ready is True


async def test_automation_dispatches_broker_start_stop_then_eod_in_order(tmp_path):
    events: list[str] = []
    session_holder = {"state": None}

    class FakeSessions:
        def active_session(self):
            return session_holder["state"]

        def acquire_broker_session(self):
            events.append("broker")
            session_holder["state"] = SimpleNamespace(
                access_token="TOKEN", capture_ready=True
            )
            return session_holder["state"]

    class FakeCapture:
        running = False

        async def start(self):
            events.append("start")
            self.running = True

        async def stop(self):
            events.append("stop")
            self.running = False

    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    capture = FakeCapture()
    automation = DailyAutomationService(
        settings,
        FakeSessions(),
        capture,
        eod_fn=lambda *_args, **_kwargs: events.append("eod"),
    )

    await automation.tick(_ms(21, 8, 30))
    await automation.tick(_ms(21, 9, 0))
    await automation.tick(_ms(21, 15, 30))

    assert events == ["broker", "start", "stop", "eod"]


async def test_eod_is_never_started_when_capture_flush_fails(tmp_path):
    events: list[str] = []

    class Sessions:
        def active_session(self):
            return None

    class FailedFlushCapture:
        running = True

        async def stop(self):
            events.append("stop")
            raise RuntimeError("writer did not flush")

    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    automation = DailyAutomationService(
        settings,
        Sessions(),
        FailedFlushCapture(),
        eod_fn=lambda *_args, **_kwargs: events.append("eod"),
    )

    await automation.tick(_ms(21, 15, 30))

    assert events == ["stop"]
    assert automation.status()["eod_completed_date"] is None


async def test_real_capture_task_failure_blocks_automated_eod(tmp_path):
    class Sessions:
        def active_session(self):
            return SimpleNamespace(
                access_token="ACCESS",
                risk_free_rate=0.065,
                capture_ready=True,
            )

    async def failing_run(_context, _stop_event):
        raise RuntimeError("writer failure")

    context = SimpleNamespace(
        trading_date="2026-07-21",
        index_tables={},
        stock_matrix=None,
        tokens=[],
        skipped_indices=[],
    )
    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    sessions = Sessions()
    controller = CaptureController(
        settings,
        sessions,
        hub=None,
        bootstrap_fn=lambda *_args, **_kwargs: context,
        run_fn=failing_run,
    )
    await controller.start()
    await asyncio.sleep(0)
    eod_calls: list[str] = []
    automation = DailyAutomationService(
        settings,
        sessions,
        controller,
        eod_fn=lambda *_args, **_kwargs: eod_calls.append("eod"),
    )

    await automation.tick(_ms(21, 15, 30))

    assert eod_calls == []
    assert automation.status()["eod_completed_date"] is None


async def test_automation_status_redacts_broker_failures(tmp_path, caplog):
    class FailedSessions:
        def active_session(self):
            return None

        def acquire_broker_session(self):
            raise RuntimeError("TOKEN_MUST_NOT_ESCAPE")

    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    automation = DailyAutomationService(
        settings,
        FailedSessions(),
        SimpleNamespace(running=False),
    )

    await automation.tick(_ms(21, 8, 30))

    status = automation.status()
    assert status["last_error"] == "shared token is not ready; retrying in the auth window"
    assert "TOKEN_MUST_NOT_ESCAPE" not in str(status)
    assert "TOKEN_MUST_NOT_ESCAPE" not in caplog.text


async def test_automation_reports_explicitly_missing_shared_session(tmp_path):
    class MissingSessions:
        def active_session(self):
            return None

        def acquire_broker_session(self):
            return None

    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    automation = DailyAutomationService(
        settings,
        MissingSessions(),
        SimpleNamespace(running=False),
    )

    await automation.tick(_ms(21, 8, 30))

    assert automation.status()["last_error"] == (
        "shared token is not ready; retrying in the auth window"
    )


async def test_eod_is_skipped_when_capture_cannot_prove_writer_flush(tmp_path):
    class Sessions:
        def active_session(self):
            return None

    class UnsafeCapture:
        running = False

        async def stop(self):
            raise RuntimeError("writer did not stop")

    eod_calls: list[str] = []
    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    automation = DailyAutomationService(
        settings,
        Sessions(),
        UnsafeCapture(),
        eod_fn=lambda *_args, **_kwargs: eod_calls.append("eod"),
    )

    await automation.tick(_ms(21, 16, 0))

    assert eod_calls == []
    assert automation.status()["eod_completed_date"] is None
    assert automation.status()["last_error"] == (
        "end-of-day compression blocked because capture did not flush safely"
    )


async def test_automation_loop_survives_and_redacts_unexpected_tick_error(
    tmp_path, caplog
):
    settings = SimpleNamespace(
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        auth_poll_interval_seconds=60,
        market_data_path=tmp_path / "live",
        archive_data_path=tmp_path / "archive",
        zstd_level=17,
    )
    automation = DailyAutomationService(
        settings,
        SimpleNamespace(),
        SimpleNamespace(running=False),
        tick_interval_s=0.001,
    )
    stop_event = asyncio.Event()
    calls = 0

    async def flaky_tick():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("SESSION_SECRET_MUST_NOT_ESCAPE")
        stop_event.set()
        return ()

    automation.tick = flaky_tick

    await asyncio.wait_for(automation.run(stop_event), timeout=0.2)

    assert calls == 2
    assert automation.status()["last_error"] == (
        "daily automation tick failed; retrying"
    )
    assert "SESSION_SECRET_MUST_NOT_ESCAPE" not in caplog.text



def test_configured_market_holiday_never_polls_or_captures():
    state = AutomationState()
    calendar = TradingCalendar(
        holidays={"2026-07-21"}, market_open="09:00", market_close="15:30"
    )

    for hour, minute in ((8, 30), (9, 0), (12, 0), (15, 30)):
        state, actions = decide_automation(
            _ms(21, hour, minute),
            state,
            calendar=calendar,
            auth_poll_start="08:30",
            auth_poll_end="09:00",
            has_valid_session=False,
            is_capture_ready=False,
            is_capture_running=False,
        )
        assert actions == ()


def test_invalidating_current_session_preserves_rate_and_rejects_stale_token(tmp_path):
    state = SessionState(
        "2026-07-21", "EXPIRED", 0.065, 1, 1, risk_free_rate_as_of="2026-07-20"
    )
    save_session(tmp_path, state)
    service = SessionService(
        _settings(tmp_path),
        clock=lambda: _ms(21, 10, 0),
        broker_fetcher=lambda: None,
        broker_validator=lambda _token: None,
    )

    assert service.invalidate_active_session("OTHER") is False
    assert load_session(tmp_path, "2026-07-21") == state
    assert service.invalidate_active_session("EXPIRED") is True
    assert load_session(tmp_path, "2026-07-21") is None

    rate = resolve_risk_free_rate(tmp_path, "2026-07-21")
    assert rate.risk_free_rate == 0.065
    assert rate.risk_free_rate_as_of == "2026-07-20"
