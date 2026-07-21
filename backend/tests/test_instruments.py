"""Tests for instrument-dump parsing and daily archiving."""

from __future__ import annotations

from app.kite.instruments import (
    InstrumentStore,
    filter_by_type,
    filter_options,
    parse_instruments_csv,
)

SAMPLE_CSV = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
    "tick_size,lot_size,instrument_type,segment,exchange\n"
    "738561,2885,RELIANCE,RELIANCE,0,,0,0.05,250,EQ,NSE,NSE\n"
    "12345678,48225,RELIANCE26JULFUT,RELIANCE,0,2026-07-31,0,0.05,250,FUT,NFO-FUT,NFO\n"
    "12345679,48226,RELIANCE26JUL2500CE,RELIANCE,0,2026-07-31,2500,0.05,250,CE,NFO-OPT,NFO\n"
    "12345680,48227,RELIANCE26JUL2500PE,RELIANCE,0,2026-07-31,2500,0.05,250,PE,NFO-OPT,NFO\n"
    "256265,,NIFTY 50,NIFTY,0,,0,0.05,0,EQ,INDICES,NSE\n"
)


def test_parse_types_and_values():
    rows = parse_instruments_csv(SAMPLE_CSV)
    assert len(rows) == 5
    eq = rows[0]
    assert eq.instrument_token == 738561
    assert eq.tradingsymbol == "RELIANCE"
    assert eq.instrument_type == "EQ"
    assert eq.lot_size == 250
    assert eq.tick_size == 0.05
    fut = rows[1]
    assert fut.instrument_type == "FUT"
    assert fut.expiry == "2026-07-31"
    ce = rows[2]
    assert ce.strike == 2500.0
    assert ce.instrument_type == "CE"


def test_filter_helpers():
    rows = parse_instruments_csv(SAMPLE_CSV)
    assert len(filter_by_type(rows, "FUT")) == 1
    opts = filter_options(rows, "RELIANCE", expiry="2026-07-31")
    assert {o.instrument_type for o in opts} == {"CE", "PE"}
    assert filter_options(rows, "RELIANCE", expiry="2099-01-01") == []


def test_fetch_archive_and_cache(tmp_path):
    calls = []

    def fake_fetch(exchange: str) -> str:
        calls.append(exchange)
        return SAMPLE_CSV

    store = InstrumentStore(tmp_path, fake_fetch)
    assert not store.is_archived("NFO", "2026-07-21")

    rows = store.fetch_and_archive("NFO", "2026-07-21")
    assert len(rows) == 5
    assert store.is_archived("NFO", "2026-07-21")
    assert store.archive_path("NFO", "2026-07-21").read_text().startswith("instrument_token")

    # get() should use the archive, not re-fetch.
    rows2 = store.get("NFO", "2026-07-21")
    assert len(rows2) == 5
    assert calls == ["NFO"]  # only the initial fetch

    # refresh=True forces a re-fetch.
    store.get("NFO", "2026-07-21", refresh=True)
    assert calls == ["NFO", "NFO"]


def test_load_archived_missing_returns_none(tmp_path):
    store = InstrumentStore(tmp_path, lambda e: SAMPLE_CSV)
    assert store.load_archived("BFO", "2026-07-21") is None
