"""Operator holidays and special sessions: store, calendar, sessions, automation, API."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.calendar import create_calendar_router
from app.ops import market_overrides
from app.ops.automation import (
    ACTION_FETCH_BROKER_TOKEN,
    ACTION_RUN_EOD,
    ACTION_START_CAPTURE,
    ACTION_STOP_CAPTURE,
    AutomationState,
    decide_automation,
)
from app.ops.calendar import IST_FALLBACK, PHASE_HOLIDAY, PHASE_OPEN, TradingCalendar
from app.ops.market_overrides import (
    Holiday,
    MarketOverrides,
    MarketOverrideStore,
    OverrideValidationError,
    SpecialSession,
)
from app.ops.sessions import (
    PHASE_AUCTION,
    PHASE_CLOSED,
    PHASE_INACTIVE,
    SESSION_EQUITY_CASH,
    SESSION_EQUITY_DERIV,
    MarketSession,
    SessionRegistry,
    parse_hhmm,
)
from app.session import SessionState, save_session

OPERATOR_TOKEN = "operator-token-for-tests"
SUNDAY = "2026-11-08"
MONDAY = "2026-11-09"


def _ms(day: str, hour: int, minute: int, second: int = 0) -> int:
    d = date.fromisoformat(day)
    return int(
        datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=IST_FALLBACK).timestamp()
        * 1000
    )


@pytest.fixture
def store(tmp_path):
    instance = MarketOverrideStore(tmp_path / "market_calendar.json")
    market_overrides.install(instance)
    yield instance
    market_overrides.install(None)


def _diwali() -> SpecialSession:
    return SpecialSession.create(SUNDAY, "14:20", "15:30", "13:00", 5, "Diwali muhurat")


# --- model + store ---------------------------------------------------------- #


def test_special_session_defaults_token_fetch_an_hour_before_capture_start():
    session = SpecialSession.create(SUNDAY, "14:20", "15:30")
    assert session.capture_start_time == parse_hhmm("14:15")
    assert session.token_fetch_at == "13:15"


@pytest.mark.parametrize(
    ("open_at", "close_at", "token_at", "message"),
    [
        ("15:30", "14:20", None, "close must be after open"),
        ("14:20", "15:30", "14:16", "token fetch time must be before"),
        ("25:00", "15:30", None, "open must be HH:MM"),
    ],
)
def test_special_session_rejects_bad_windows(open_at, close_at, token_at, message):
    with pytest.raises(OverrideValidationError, match=message):
        SpecialSession.create(SUNDAY, open_at, close_at, token_at)


def test_a_date_cannot_be_both_holiday_and_special_session():
    overrides = MarketOverrides().with_special(_diwali())
    with pytest.raises(OverrideValidationError, match="special session"):
        overrides.with_holiday(Holiday.create(SUNDAY))
    overrides = MarketOverrides().with_holiday(Holiday.create(MONDAY))
    with pytest.raises(OverrideValidationError, match="holiday"):
        overrides.with_special(SpecialSession.create(MONDAY, "10:00", "11:00"))


def test_store_round_trips_and_returns_new_objects(store):
    original = MarketOverrides()
    updated = original.with_special(_diwali()).with_holiday(Holiday.create(MONDAY, "x"))
    assert original == MarketOverrides()  # immutable
    store.save(updated)
    assert store.reload() == updated
    assert store.reload().without_special(SUNDAY).special_sessions == ()


def test_store_keeps_last_good_copy_when_file_is_corrupted(store):
    store.save(MarketOverrides().with_special(_diwali()))
    store.path.write_text("{not json")
    assert store.reload().special_for(SUNDAY) == _diwali()


# --- calendar ---------------------------------------------------------------- #


def test_special_session_opens_a_weekend_and_uses_its_own_window(store):
    calendar = TradingCalendar(market_open="09:15", market_close="15:40")
    assert not calendar.is_trading_day(_ms(SUNDAY, 14, 30))
    store.save(MarketOverrides().with_special(_diwali()))
    store.reload()
    assert calendar.is_trading_day(_ms(SUNDAY, 14, 30))
    assert calendar.phase(_ms(SUNDAY, 14, 0)) != PHASE_OPEN
    assert calendar.phase(_ms(SUNDAY, 14, 30)) == PHASE_OPEN
    assert calendar.phase(_ms(SUNDAY, 15, 35)) != PHASE_OPEN


def test_operator_holiday_closes_a_weekday(store):
    calendar = TradingCalendar()
    store.save(MarketOverrides().with_holiday(Holiday.create(MONDAY)))
    store.reload()
    assert calendar.phase(_ms(MONDAY, 10, 0)) == PHASE_HOLIDAY


def test_calendar_without_installed_store_is_unchanged():
    market_overrides.install(None)
    assert not TradingCalendar().is_trading_day(_ms(SUNDAY, 10, 0))


# --- market sessions --------------------------------------------------------- #


def _registry() -> SessionRegistry:
    calendar = TradingCalendar(market_open="09:10", market_close="15:40")
    deriv = MarketSession(
        SESSION_EQUITY_DERIV, parse_hhmm("09:15"), parse_hhmm("15:40"),
        pre_open_start=parse_hhmm("09:00"), pre_open_end=parse_hhmm("09:15"),
        capture_pre_open=True,
    )
    cash = MarketSession(
        SESSION_EQUITY_CASH, parse_hhmm("09:15"), parse_hhmm("15:15"),
        cas_start=parse_hhmm("15:15"), cas_end=parse_hhmm("15:35"),
    )
    return SessionRegistry(
        calendar,
        {SESSION_EQUITY_DERIV: deriv, SESSION_EQUITY_CASH: cash},
        {"NIFTY": SESSION_EQUITY_DERIV, "CASH": SESSION_EQUITY_CASH},
    )


def test_registry_owes_only_the_special_window_on_a_special_day(store):
    store.save(MarketOverrides().with_special(_diwali()))
    store.reload()
    registry = _registry()
    assert registry.phase("NIFTY", _ms(SUNDAY, 14, 0)) != PHASE_INACTIVE
    assert registry.is_capture_expected("NIFTY", _ms(SUNDAY, 14, 25))
    assert not registry.is_capture_expected("NIFTY", _ms(SUNDAY, 14, 10))
    # No closing auction on a special day: cash closes with everything else.
    assert registry.phase("CASH", _ms(SUNDAY, 15, 20)) != PHASE_AUCTION
    assert registry.phase("CASH", _ms(SUNDAY, 15, 31)) == PHASE_CLOSED
    assert registry.scheduled_seconds("NIFTY", _ms(SUNDAY, 12, 0)) == 70 * 60
    windows = registry.scheduled_windows_on("NIFTY", date.fromisoformat(SUNDAY))
    assert windows == ((_ms(SUNDAY, 14, 20), _ms(SUNDAY, 15, 30)),)
    assert registry.auction_windows_on("CASH", date.fromisoformat(SUNDAY)) == ()
    assert registry.scheduled_seconds_between(
        "NIFTY", _ms(SUNDAY, 0, 0), _ms(SUNDAY, 23, 0)
    ) == 70 * 60


def test_registry_regular_day_is_untouched_by_special_sessions(store):
    store.save(MarketOverrides().with_special(_diwali()))
    store.reload()
    registry = _registry()
    assert registry.scheduled_seconds("NIFTY", _ms(MONDAY, 12, 0)) == (6 * 60 + 40) * 60
    assert registry.phase("CASH", _ms(MONDAY, 15, 20)) == PHASE_AUCTION


# --- automation -------------------------------------------------------------- #


def _decide(now: int, state: AutomationState, **kwargs):
    defaults = dict(
        has_valid_session=False,
        is_capture_ready=False,
        is_capture_running=False,
    )
    defaults.update(kwargs)
    return decide_automation(
        now,
        state,
        calendar=TradingCalendar(market_open="09:10", market_close="15:40"),
        auth_poll_start="08:31",
        auth_poll_end="09:09",
        broker_retry_interval_s=60,
        eod_tail_seconds=120,
        **defaults,
    )


def test_automation_fetches_token_starts_capture_and_runs_eod_on_special_day(store):
    store.save(MarketOverrides().with_special(_diwali()))
    store.reload()
    state = AutomationState()

    state, actions = _decide(_ms(SUNDAY, 9, 0), state)
    assert actions == ()  # the regular token window does not apply
    state, actions = _decide(_ms(SUNDAY, 12, 59), state)
    assert actions == ()
    state, actions = _decide(_ms(SUNDAY, 13, 0), state)
    assert actions == (ACTION_FETCH_BROKER_TOKEN,)

    state, actions = _decide(_ms(SUNDAY, 14, 14), state, has_valid_session=True,
                             is_capture_ready=True)
    assert actions == ()
    state, actions = _decide(_ms(SUNDAY, 14, 15), state, has_valid_session=True,
                             is_capture_ready=True)
    assert actions == (ACTION_START_CAPTURE,)

    state, actions = _decide(_ms(SUNDAY, 15, 30), state, has_valid_session=True,
                             is_capture_running=True)
    assert actions == (ACTION_STOP_CAPTURE,)
    state, actions = _decide(_ms(SUNDAY, 15, 32), state, has_valid_session=True)
    assert actions == (ACTION_STOP_CAPTURE, ACTION_RUN_EOD)
    assert state.eod_in_progress_date == SUNDAY


def test_automation_skips_operator_holiday(store):
    store.save(MarketOverrides().with_holiday(Holiday.create(MONDAY)))
    store.reload()
    _, actions = _decide(_ms(MONDAY, 8, 40), AutomationState())
    assert actions == ()


# --- API --------------------------------------------------------------------- #


class _FakeService:
    def __init__(self, tmp_path, now_ms: int, fetch=None):
        self.settings = SimpleNamespace(
            release_maintenance_token=OPERATOR_TOKEN,
            market_holidays=["2026-11-10"],
            market_open="09:15",
            market_close="15:40",
            capture_window=("09:10", "15:40"),
            auth_poll_start="08:31",
            auth_poll_end="09:09",
            state_dir=tmp_path,
        )
        self.calendar = TradingCalendar(market_open="09:15", market_close="15:40")
        self._clock = lambda: now_ms
        self._fetch = fetch
        self._tmp = tmp_path

    def trading_date(self) -> str:
        return self.calendar.trading_date(self._clock())

    def active_session(self):
        from app.session import load_session

        return load_session(self._tmp, self.trading_date())

    def acquire_broker_session(self):
        return self._fetch() if self._fetch else None


def _client(service) -> TestClient:
    app = FastAPI()
    app.state.session_service = service
    app.include_router(create_calendar_router())
    return TestClient(app)


AUTH = {"X-Release-Maintenance-Token": OPERATOR_TOKEN}


def test_api_writes_require_the_operator_token(store, tmp_path):
    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30)))
    body = {"date": SUNDAY, "open": "14:20", "close": "15:30"}
    assert client.put("/api/calendar/special-sessions", json=body).status_code == 401
    assert client.put(
        "/api/calendar/special-sessions", json=body,
        headers={"X-Release-Maintenance-Token": "wrong"},
    ).status_code == 401
    assert client.post("/api/calendar/fetch-token").status_code == 401


def test_api_adds_special_session_and_reports_today(store, tmp_path):
    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30)))
    response = client.put(
        "/api/calendar/special-sessions",
        json={"date": SUNDAY, "open": "14:20", "close": "15:30",
              "token_fetch_at": "13:00", "note": "Diwali"},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["special_sessions"][0]["open"] == "14:20"
    assert payload["env_holidays"] == ["2026-11-10"]
    today = payload["today"]
    assert today["is_trading_day"] is True
    assert today["token_window"] == ["13:00", "14:15"]
    assert today["capture_window"] == ["14:15", "15:30"]
    assert store.path.exists()

    response = client.delete(f"/api/calendar/special-sessions/{SUNDAY}", headers=AUTH)
    assert response.json()["special_sessions"] == []


def test_api_rejects_past_dates_and_invalid_windows(store, tmp_path):
    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30)))
    past = client.post(
        "/api/calendar/holidays", json={"date": "2026-11-01"}, headers=AUTH
    )
    assert past.status_code == 422
    bad = client.put(
        "/api/calendar/special-sessions",
        json={"date": SUNDAY, "open": "15:30", "close": "14:20"},
        headers=AUTH,
    )
    assert bad.status_code == 422
    assert "close must be after open" in bad.json()["detail"]


def test_api_holiday_add_and_remove(store, tmp_path):
    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30)))
    added = client.post(
        "/api/calendar/holidays", json={"date": MONDAY, "note": "test"}, headers=AUTH
    )
    assert added.json()["holidays"] == [{"date": MONDAY, "note": "test"}]
    removed = client.delete(f"/api/calendar/holidays/{MONDAY}", headers=AUTH)
    assert removed.json()["holidays"] == []


def test_api_fetch_token_reports_missing_and_successful_tokens(store, tmp_path):
    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30)))
    assert client.post("/api/calendar/fetch-token", headers=AUTH).status_code == 409

    def _fetch():
        state = SessionState(SUNDAY, "ACCESS", 0.07, 1, 1)
        save_session(tmp_path, state)
        return state

    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30), fetch=_fetch))
    response = client.post("/api/calendar/fetch-token", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["today"]["token_ready"] is True


# --- review follow-ups ------------------------------------------------------- #


def test_one_bad_entry_does_not_drop_the_special_session(store):
    store.path.write_text(
        '{"holidays": [{"date": "not-a-date"}], "special_sessions": ['
        '{"date": "2026-11-08", "open": "14:20", "close": "15:30", "token_fetch_at": "13:00"},'
        '{"date": "2026-11-09", "open": "15:00", "close": "14:00"}]}'
    )
    loaded = store.reload()
    assert loaded.special_for(SUNDAY) is not None
    assert loaded.special_for(MONDAY) is None
    assert len(store.errors) == 2


def test_invalid_json_is_reported_once_and_keeps_last_copy(store, caplog):
    store.save(MarketOverrides().with_special(_diwali()))
    store.path.write_text("{broken")
    store.reload()
    store.reload()
    assert store.load().special_for(SUNDAY) == _diwali()
    assert store.errors and "not valid JSON" in store.errors[0]
    assert sum("not valid JSON" in r.message for r in caplog.records) == 1


def test_api_reports_load_errors_and_save_failures(store, tmp_path, monkeypatch):
    store.path.write_text('{"holidays": [{"date": "nope"}]}')
    store.reload()
    client = _client(_FakeService(tmp_path, _ms(SUNDAY, 9, 30)))
    assert client.get("/api/calendar").json()["load_errors"]

    def _fail(_overrides):
        raise PermissionError("read-only")

    monkeypatch.setattr(store, "save", _fail)
    response = client.post("/api/calendar/holidays", json={"date": MONDAY}, headers=AUTH)
    assert response.status_code == 503
