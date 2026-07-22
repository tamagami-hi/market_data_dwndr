"""Tests for md-capture session resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.capture.run import resolve_session


class FakeService:
    def __init__(self, session):
        self._session = session

    def trading_date(self):
        return "2026-07-21"

    def active_session(self):
        return self._session


def test_resolve_session_returns_token_and_rate():
    session = SimpleNamespace(access_token="ACCESS", risk_free_rate=0.0691)
    assert resolve_session(FakeService(session)) == ("ACCESS", 0.0691)


def test_resolve_session_requires_login():
    with pytest.raises(RuntimeError, match="run `md-login` first"):
        resolve_session(FakeService(None))


def test_resolve_session_rejects_empty_token():
    session = SimpleNamespace(access_token="", risk_free_rate=0.07)
    with pytest.raises(RuntimeError, match="no access_token"):
        resolve_session(FakeService(session))


def test_resolve_session_rejects_stale_risk_free_rate():
    session = SimpleNamespace(
        access_token="ACCESS",
        risk_free_rate=0.065,
        capture_ready=False,
        rate_update_required=True,
    )

    with pytest.raises(RuntimeError, match="risk-free rate update is required"):
        resolve_session(FakeService(session))
