"""Regression tests for the bugs confirmed in the 2026-11-08 backend bug hunt."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api import health as health_module
from app.api.auth import create_auth_router
from app.api.calendar import create_calendar_router
from app.api.capture import CaptureController, create_capture_router
from app.api.health import CODE_DEAD, CODE_STALE, build_health
from app.capture.maintenance import MaintenanceLeaseStore
from app.config import Settings
from app.ops import market_overrides
from app.ops.calendar import IST_FALLBACK, TradingCalendar
from app.ops.market_overrides import (
    MarketOverrides,
    MarketOverrideStore,
    OverrideValidationError,
    SpecialSession,
)

OPERATOR_TOKEN = "operator-token-for-tests-0123456789abcdef"
AUTH = {"X-Release-Maintenance-Token": OPERATOR_TOKEN}
SUNDAY = "2026-11-08"
MONDAY = "2026-11-09"


def _ms(day: str, hour: int, minute: int) -> int:
    d = date.fromisoformat(day)
    moment = datetime(d.year, d.month, d.day, hour, minute, tzinfo=IST_FALLBACK)
    return int(moment.timestamp() * 1000)


@pytest.fixture
def store(tmp_path):
    instance = MarketOverrideStore(tmp_path / "market_calendar.json")
    market_overrides.install(instance)
    yield instance
    market_overrides.install(None)


def _special_json(**fields) -> dict:
    return {"date": SUNDAY, "open": "14:20", "close": "15:30", "token_fetch_at": "13:00", **fields}


# --- calendar store: one bad entry must never drop the rest ------------------ #


@pytest.mark.parametrize("lead", ["five", "10m", float("nan"), float("inf"), True, 2.5])
def test_bad_capture_lead_skips_only_that_entry(store, lead):
    import json

    payload = {
        "holidays": [],
        "special_sessions": [
            _special_json(),
            {"date": MONDAY, "open": "10:00", "close": "11:00", "capture_lead_minutes": lead},
        ],
    }
    store.path.write_text(json.dumps(payload))
    loaded = store.reload()
    assert loaded.special_for(SUNDAY) is not None
    assert loaded.special_for(MONDAY) is None
    assert any("capture lead" in error for error in store.errors)
    assert TradingCalendar().is_trading_day(_ms(SUNDAY, 14, 30))


def test_non_list_sections_are_reported_not_raised(store):
    import json

    store.path.write_text(json.dumps({"holidays": 5, "special_sessions": [_special_json()]}))
    loaded = store.reload()
    assert loaded.special_for(SUNDAY) is not None
    assert any("'holidays' is not a list" in error for error in store.errors)


def test_active_overrides_falls_back_to_last_good_copy_when_load_raises(store, monkeypatch):
    store.save(MarketOverrides().with_special(SpecialSession.create(SUNDAY, "14:20", "15:30")))

    def _boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "load", _boom)
    assert market_overrides.active_overrides().special_for(SUNDAY) is not None


def test_special_close_must_leave_room_for_eod():
    with pytest.raises(OverrideValidationError, match="at or before 23:30"):
        SpecialSession.create(SUNDAY, "22:00", "23:58")
    assert SpecialSession.create(SUNDAY, "22:00", "23:30").close == "23:30"


def test_ui_write_keeps_a_copy_of_a_problem_file(store):
    store.path.write_text('{"holidays": [{"date": "2026-12-01",}]}')  # trailing comma
    store.reload()
    store.save(MarketOverrides().with_special(SpecialSession.create(SUNDAY, "14:20", "15:30")))
    backups = list(store.path.parent.glob("market_calendar.rejected-*.json"))
    assert len(backups) == 1
    assert "2026-12-01" in backups[0].read_text()
    assert store.errors == ()


# --- calendar API ---------------------------------------------------------------- #


class _CalendarService:
    def __init__(self, tmp_path: Path):
        self.settings = SimpleNamespace(
            release_maintenance_token=OPERATOR_TOKEN,
            market_holidays=[],
            market_open="09:15",
            market_close="15:40",
            capture_window=("09:10", "15:40"),
            auth_poll_start="08:31",
            auth_poll_end="09:09",
        )
        self.calendar = TradingCalendar()
        self._clock = lambda: _ms(SUNDAY, 9, 30)

    def trading_date(self) -> str:
        return SUNDAY

    def active_session(self):
        return None


def test_impossible_date_is_422_not_500(store, tmp_path):
    app = FastAPI()
    app.state.session_service = _CalendarService(tmp_path)
    app.include_router(create_calendar_router())
    client = TestClient(app)
    for path, method, body in (
        ("/api/calendar/holidays", "post", {"date": "2026-02-30"}),
        (
            "/api/calendar/special-sessions",
            "put",
            {"date": "2026-02-30", "open": "10:00", "close": "11:00"},
        ),
    ):
        response = getattr(client, method)(path, json=body, headers=AUTH)
        assert response.status_code == 422, response.text


# --- maintenance lease after a failed capture ------------------------------------ #


def test_maintenance_lease_is_granted_and_releasable_after_failed_capture(tmp_path):
    settings = SimpleNamespace(
        state_dir=tmp_path,
        release_maintenance_token=SecretStr(OPERATOR_TOKEN),
        release_maintenance_ttl_seconds=30,
    )
    controller = CaptureController(
        settings,
        SimpleNamespace(active_session=lambda: None),
        hub=None,
        maintenance_store=MaintenanceLeaseStore(tmp_path, ttl_seconds=30),
    )
    controller._has_failed = True  # a capture task that already crashed
    app = FastAPI()
    app.state.capture_controller = controller
    app.include_router(create_capture_router())
    client = TestClient(app)

    acquired = client.post("/api/capture/maintenance", headers=AUTH)
    assert acquired.status_code == 200, acquired.text
    lease_id = acquired.json()["lease_id"]
    released = client.delete(f"/api/capture/maintenance/{lease_id}", headers=AUTH)
    assert released.status_code == 200


# --- manual login rate bound ------------------------------------------------------ #


def test_manual_login_rejects_percent_rate_before_spending_the_request_token():
    exchanged: list[str] = []
    service = SimpleNamespace(
        settings=SimpleNamespace(release_maintenance_token=OPERATOR_TOKEN),
        login=lambda **kwargs: exchanged.append(kwargs["request_token"]),
    )
    app = FastAPI()
    app.state.session_service = service
    app.include_router(create_auth_router())
    response = TestClient(app).post(
        "/api/auth/login",
        json={"request_token": "RT1", "risk_free_rate": 6.5},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert exchanged == []


# --- /health: capture down with a healthy token ------------------------------------ #


def test_health_escalates_when_capture_is_down_despite_a_healthy_token(monkeypatch):
    service = SimpleNamespace(
        status=lambda: {"market_phase": "OPEN", "auth_state": "OK", "auth_blocked_seconds": 0},
        calendar=TradingCalendar(market_open="09:15", market_close="15:40"),
    )
    state = SimpleNamespace(
        capture_controller=SimpleNamespace(
            running=False, has_failed=False, monitor_snapshot=lambda: None
        ),
        session_service=service,
    )
    monkeypatch.setattr(health_module, "now_ms", lambda: _ms(MONDAY, 9, 16))
    payload, status = build_health(state)
    check = next(c for c in payload["checks"] if c["component"] == "broker_session")
    assert check["code"] == CODE_STALE and status == 200

    monkeypatch.setattr(health_module, "now_ms", lambda: _ms(MONDAY, 13, 15))
    payload, status = build_health(state)
    check = next(c for c in payload["checks"] if c["component"] == "broker_session")
    assert check["code"] == CODE_DEAD and status == 503


# --- config: reject schedules the runtime cannot run ------------------------------- #

_REQUIRED = {
    "KITE_API_KEY": "k",
    "KITE_API_SECRET": "s",
    "MARKET_DATA_PATH": "/tmp/md-live",
    "ARCHIVE_DATA_PATH": "/tmp/md-archive",
    "HTTP_PORT": "9000",
    "FRONTEND_URL": "http://localhost:3000",
}


_ISOLATED_PREFIXES = (
    "EQUITY_", "MARKET_", "CAPTURE_", "AUTH_POLL", "RISK_FREE", "TIMEZONE", "KITE_TOKEN"
)


def _settings(monkeypatch, **env) -> Settings:
    import os

    for key in list(os.environ):
        if key.startswith(_ISOLATED_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    for key, value in {**_REQUIRED, **env}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_deployed_schedule_is_still_accepted(monkeypatch):
    settings = _settings(
        monkeypatch,
        RISK_FREE_RATE="0.064",
        MARKET_TIMEZONE="Asia/Kolkata",
        CAPTURE_START_TIME="09:10",
        CAPTURE_END_TIME="15:40",
        MARKET_OPEN="09:15",
        MARKET_CLOSE="15:40",
        EQUITY_DERIV_PREOPEN_START="09:00",
        EQUITY_DERIV_PREOPEN_END="09:15",
        EQUITY_DERIV_OPEN="09:15",
        EQUITY_DERIV_CLOSE="15:40",
        EQUITY_CASH_OPEN="09:15",
        EQUITY_CASH_CLOSE="15:15",
        EQUITY_CASH_CAS_START="15:15",
        EQUITY_CASH_CAS_END="15:35",
        AUTH_POLL_START="08:31",
        AUTH_POLL_END="09:09",
    )
    assert settings.risk_free_rate == pytest.approx(0.064)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"RISK_FREE_RATE": "6.5"}, "less than or equal to 1"),
        ({"TIMEZONE": "Asia/Kolkatta"}, "unknown TIMEZONE"),
        ({"CAPTURE_START_TIME": "09:10", "CAPTURE_END_TIME": "15:40",
          "MARKET_OPEN": "15:40", "MARKET_CLOSE": "09:15"}, "MARKET_OPEN must be before"),
        ({"EQUITY_CASH_CLOSE": "15:20"}, "invalid EQUITY_"),
    ],
)
def test_unrunnable_config_fails_at_startup(monkeypatch, env, message):
    with pytest.raises(ValidationError, match=message):
        _settings(monkeypatch, **env)
