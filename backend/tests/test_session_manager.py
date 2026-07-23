"""Tests for session orchestration + mid-day restart/resume (Phase 5 DoD)."""

from __future__ import annotations

from datetime import datetime

import numpy as np

from app.bin_codec import writer
from app.bin_codec.layout import IndexFrame, IndexHeader, RawBlock
from app.bin_codec.reader import IndexBinReader
from app.kite.auth import KiteAuthenticator
from app.ops.calendar import IST_FALLBACK, TradingCalendar
from app.ops.session_manager import SessionManager


def _ms(y, mo, d, h, mi) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=IST_FALLBACK).timestamp() * 1000)


def _manager(tmp_path, generator, now_ms_value):
    auth = KiteAuthenticator(
        "key", "secret", tmp_path, session_generator=generator, clock=lambda: now_ms_value
    )
    cal = TradingCalendar()
    return SessionManager(auth, cal, clock=lambda: now_ms_value)


def test_login_then_resume_without_reprompt(tmp_path):
    now = _ms(2026, 7, 21, 6, 30)
    logins = {"count": 0}

    def generator(request_token, api_secret):
        logins["count"] += 1
        return {"access_token": "TOKEN_DAY1"}

    # First start of the day -> login.
    mgr = _manager(tmp_path, generator, now)
    assert mgr.active_session() is None
    state = mgr.resume_or_login(request_token="reqtok", risk_free_rate=0.0691)
    assert state.access_token == "TOKEN_DAY1"
    assert state.trading_date == "2026-07-21"
    assert logins["count"] == 1

    # Mid-day restart -> resume, no second login.
    later = _manager(tmp_path, generator, _ms(2026, 7, 21, 12, 0))
    resumed = later.resume_or_login()
    assert resumed.access_token == "TOKEN_DAY1"
    assert resumed.risk_free_rate == 0.0691
    assert logins["count"] == 1  # unchanged


def test_midday_restart_appends_without_duplicate_header(tmp_path):
    now = _ms(2026, 7, 21, 6, 30)
    mgr = _manager(
        tmp_path, lambda r, s: {"access_token": "TOK"}, now
    )
    session = mgr.resume_or_login(request_token="rt", risk_free_rate=0.0691)

    strikes = np.array([2_450_000, 2_455_000], dtype="<i8")
    n = strikes.shape[0]

    def header() -> IndexHeader:
        # risk-free rate stamped from session state
        return IndexHeader("2026-07-21", "NIFTY", "2026-07-24", session.risk_free_rate, strikes)

    def frame(seq: int) -> IndexFrame:
        return IndexFrame(1_753_070_400_000 + seq, seq, 0, 0, RawBlock.zeros(n), RawBlock.zeros(n))

    path = tmp_path / "INDICES" / "NIFTY" / "2026-07-21.bin"

    # Morning: write header + 2 frames.
    with writer.IndexBinWriter(path) as w:
        assert w.write_header(header()) is True
        w.append_frame(frame(0))
        w.append_frame(frame(1))

    # Restart: resume session, reopen same file, header must NOT be rewritten.
    resumed = mgr.resume_or_login()
    assert resumed.risk_free_rate == 0.0691
    with writer.IndexBinWriter(path) as w:
        assert w.write_header(header()) is False  # header-once
        w.append_frame(frame(2))

    with IndexBinReader(path) as r:
        assert len(r) == 3  # all frames, single header
        assert [f.sequence for f in r.frames()] == [0, 1, 2]
        assert r.header().risk_free_rate == 0.0691
