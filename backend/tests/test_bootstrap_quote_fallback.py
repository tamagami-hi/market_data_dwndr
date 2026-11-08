"""A restart whose LTP quote fails must not drop every index option chain.

2026-11-05: during a network outage every restarted process logged "LTP quote failed",
skipped all six index chains ("spot must be > 0 before assembly") and started capture with
stocks only. Had the ticker connected, the rest of the day would have had no index data.
"""

from __future__ import annotations

import logging

import pytest

from app.bin_codec.scan import last_index_spot_paise
from app.capture.bootstrap import bootstrap_capture
from app.ops.calendar import TradingCalendar
from tests.test_bootstrap import FakeStore, _quote_fn, _settings
from tests.test_restart_resume import _write_frames

CLOCK_MS = 1_753_070_400_000
GOOD_QUOTES = {"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.3}


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr("app.capture.bootstrap.QUOTE_RETRY_DELAY_S", 0)


def _trading_date() -> str:
    return TradingCalendar().trading_date(CLOCK_MS)


def _bootstrap(tmp_path, quote_fn):
    return bootstrap_capture(
        _settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=quote_fn,
        clock=lambda: CLOCK_MS,
    )


def _failing_quote(calls: list[int] | None = None):
    def _quote(symbols):
        if calls is not None:
            calls.append(1)
        raise ConnectionError("Temporary failure in name resolution")

    return _quote


def _write_day_file(tmp_path, spot_rupees: float) -> None:
    """Frames a previous generation wrote today, with the spot it was seeing."""
    table = _bootstrap(tmp_path, _quote_fn(GOOD_QUOTES)).index_tables["NIFTY"]
    table.spot_price = int(round(spot_rupees * 100))
    path = tmp_path / "INDICES" / "NIFTY" / f"{_trading_date()}.bin"
    _write_frames(path, table, 3, start_ts=CLOCK_MS - 60_000)


def test_last_index_spot_reads_the_newest_frame(tmp_path):
    _write_day_file(tmp_path, 24_610.0)
    path = tmp_path / "INDICES" / "NIFTY" / f"{_trading_date()}.bin"
    assert last_index_spot_paise(path) == 2_461_000


def test_last_index_spot_is_none_without_frames(tmp_path):
    assert last_index_spot_paise(tmp_path / "missing.bin") is None


def test_restart_rebuilds_the_chain_from_todays_file_when_the_quote_fails(tmp_path, caplog):
    _write_day_file(tmp_path, 24_610.0)
    caplog.set_level(logging.INFO, logger="app.capture.bootstrap")

    ctx = _bootstrap(tmp_path, _failing_quote())

    assert "NIFTY" in ctx.index_tables
    assert ctx.skipped_indices == []
    assert ctx.index_tables["NIFTY"].chain.n_strikes == 21
    assert ctx.quote_error is not None  # still recorded for the incident ledger
    assert any("last spot written today" in r.message for r in caplog.records)


def test_a_transient_quote_failure_is_retried_once(tmp_path):
    attempts: list[int] = []
    good = _quote_fn(GOOD_QUOTES)

    def _flaky(symbols):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("Temporary failure in name resolution")
        return good(symbols)

    ctx = _bootstrap(tmp_path, _flaky)
    assert len(attempts) == 2
    assert "NIFTY" in ctx.index_tables
    assert ctx.quote_error is None


def test_a_fresh_day_without_a_quote_still_skips_with_the_reason(tmp_path):
    calls: list[int] = []
    ctx = _bootstrap(tmp_path, _failing_quote(calls))
    assert len(calls) == 2  # one retry, then give up
    assert ctx.skipped_indices == ["NIFTY"]
    assert "no spot" in ctx.skip_reasons["NIFTY"]


@pytest.mark.parametrize("spot", [0.0, -5.0])
def test_a_non_positive_spot_on_disk_is_not_used(tmp_path, spot):
    _write_day_file(tmp_path, spot)
    ctx = _bootstrap(tmp_path, _failing_quote())
    assert ctx.skipped_indices == ["NIFTY"]
