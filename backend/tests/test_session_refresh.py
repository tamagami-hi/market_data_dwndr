"""Tests for SessionService.refresh_broker_session (non-destructive token swap)."""

from __future__ import annotations

import threading
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


def test_refresh_broker_session_fetches_then_swaps_in_a_new_token(tmp_path):
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


def test_a_failed_refresh_keeps_the_working_session(tmp_path):
    """The 2026-08-06 defect: refresh deleted the day's token before asking for one.

    calspread answered ``authenticated: false`` on every attempt of every recorded day
    (``token_refreshes`` was 0 throughout), so 27 attempts left the deployment with no
    session at all and capture unable to start. A refresh that obtains nothing must be a
    no-op.
    """
    state_dir = tmp_path / "_state"
    state_dir.mkdir()

    tokens = iter(["GOOD"])
    service = SessionService(
        _settings(state_dir),
        broker_fetcher=lambda: next(tokens, None),
        broker_validator=lambda _token: None,
        rate_resolver=lambda: 0.07,
    )
    assert service.acquire_broker_session().access_token == "GOOD"

    # Broker now has nothing to hand back (the fetcher is exhausted).
    assert service.refresh_broker_session("GOOD") is None

    # The working session is untouched, so capture can still run/restart.
    assert service.active_session() is not None
    assert service.active_session().access_token == "GOOD"


def test_refresh_does_not_swap_when_the_broker_returns_the_same_token(tmp_path):
    state_dir = tmp_path / "_state"
    state_dir.mkdir()
    validated: list[str] = []

    service = SessionService(
        _settings(state_dir),
        broker_fetcher=lambda: "SAME",
        broker_validator=validated.append,
        rate_resolver=lambda: 0.07,
    )
    assert service.acquire_broker_session().access_token == "SAME"
    validated.clear()

    assert service.refresh_broker_session("SAME") is None
    assert validated == []  # nothing gained, nothing revalidated
    assert service.active_session().access_token == "SAME"


def test_refresh_survives_a_validator_failure_without_losing_the_session(tmp_path):
    state_dir = tmp_path / "_state"
    state_dir.mkdir()

    tokens = iter(["GOOD", "REJECTED"])

    def validator(token: str) -> None:
        if token == "REJECTED":
            raise RuntimeError("Kite rejected the token")

    service = SessionService(
        _settings(state_dir),
        broker_fetcher=lambda: next(tokens, None),
        broker_validator=validator,
        rate_resolver=lambda: 0.07,
    )
    assert service.acquire_broker_session().access_token == "GOOD"

    assert service.refresh_broker_session("GOOD") is None
    assert service.active_session().access_token == "GOOD"


def test_refresh_gives_up_rather_than_blocking_on_a_busy_session_lock(tmp_path):
    """The recovery path must never queue behind a login holding the lock."""
    state_dir = tmp_path / "_state"
    state_dir.mkdir()

    service = SessionService(
        _settings(state_dir),
        broker_fetcher=lambda: "NEW",
        broker_validator=lambda _t: None,
        rate_resolver=lambda: 0.07,
    )
    holder = threading.Thread(target=_hold_lock, args=(service,), daemon=True)
    holder.start()
    _LOCK_HELD.wait(timeout=5)
    try:
        import app.session_service as module

        original = module.SESSION_LOCK_TIMEOUT_S
        module.SESSION_LOCK_TIMEOUT_S = 0.05
        try:
            assert service.refresh_broker_session("OLD") is None
        finally:
            module.SESSION_LOCK_TIMEOUT_S = original
    finally:
        _RELEASE_LOCK.set()
        holder.join(timeout=5)


_LOCK_HELD = threading.Event()
_RELEASE_LOCK = threading.Event()


def _hold_lock(service: SessionService) -> None:
    with service._session_lock:  # noqa: SLF001 - deliberately simulating contention
        _LOCK_HELD.set()
        _RELEASE_LOCK.wait(timeout=10)
