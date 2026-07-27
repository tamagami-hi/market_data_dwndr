"""Tests for SessionService.refresh_broker_session (reconnect token refresh)."""

from __future__ import annotations

from types import SimpleNamespace

from app.session_service import SessionService


def _settings(state_dir):
    return SimpleNamespace(
        market_holidays=[],
        timezone="Asia/Kolkata",
        market_open="09:00",
        market_close="15:30",
        state_dir=state_dir,
        kite_api_key="key",
        kite_user_id="AB1234",
    )


def test_refresh_broker_session_invalidates_then_fetches_a_new_token(tmp_path):
    state_dir = tmp_path / "_state"
    state_dir.mkdir()

    tokens = iter(["OLD", "NEW"])
    fetched: list[str] = []
    validated: list[str] = []

    def fetcher() -> str:
        token = next(tokens)
        fetched.append(token)
        return token

    def validator(token: str) -> None:
        validated.append(token)

    service = SessionService(
        _settings(state_dir),
        broker_fetcher=fetcher,
        broker_validator=validator,
        rate_resolver=lambda: 0.07,
    )

    first = service.acquire_broker_session()
    assert first is not None and first.access_token == "OLD"

    # A plain re-acquire would short-circuit on the persisted "OLD" token; refresh must
    # discard it and hit the broker (calspread) again for a genuinely new token.
    refreshed = service.refresh_broker_session("OLD")
    assert refreshed is not None and refreshed.access_token == "NEW"

    assert fetched == ["OLD", "NEW"]
    assert "NEW" in validated
    # The active persisted session is now the fresh token.
    assert service.active_session().access_token == "NEW"


def test_refresh_broker_session_returns_none_when_broker_unauthenticated(tmp_path):
    state_dir = tmp_path / "_state"
    state_dir.mkdir()

    service = SessionService(
        _settings(state_dir),
        broker_fetcher=lambda: None,  # broker has nothing authenticated yet
        broker_validator=lambda _token: None,
        rate_resolver=lambda: 0.07,
    )

    assert service.refresh_broker_session("OLD") is None
