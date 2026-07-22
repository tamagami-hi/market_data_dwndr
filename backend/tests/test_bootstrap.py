"""Tests for the live capture bootstrap wiring (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.capture.bootstrap import bootstrap_capture
from app.chain.config import VIX_TOKEN, get_index_config
from tests.test_board import _sample_instruments
from tests.test_chain import _make_options


class FakeStore:
    """InstrumentStore stand-in: NFO carries index options + stock futures; NSE = equities."""

    def __init__(self):
        nfo_futs, nse_eq = _sample_instruments()
        nifty_options = _make_options("NIFTY", "2026-07-31", list(range(24000, 25001, 50)))
        self._by_exchange = {
            "NFO": nifty_options + nfo_futs,
            "NSE": nse_eq,
            "BFO": [],
        }

    def get(self, exchange, trading_date, refresh=False):
        return self._by_exchange.get(exchange, [])


def _settings(tmp_path, indices=("NIFTY",), stock_universe="all"):
    return SimpleNamespace(
        kite_api_key="apikey",
        kite_static_ip=None,
        kite_http_proxy=None,
        indices=list(indices),
        stock_universe=stock_universe,
        market_holidays=[],
        timezone="Asia/Kolkata",
        market_open="09:15",
        market_close="15:30",
        indices_dir=tmp_path / "INDICES",
        stocks_dir=tmp_path / "STOCKS",
        market_data_path=tmp_path,
    )


def _quote_fn(prices):
    return lambda symbols: {s: prices[s] for s in symbols if s in prices}


class FakeHub:
    async def broadcast(self, topic, message):
        return 1


def test_bootstrap_wires_index_and_stocks(tmp_path):
    ctx = bootstrap_capture(
        _settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.3}),
        clock=lambda: 1_753_070_400_000,
    )

    # index chain built for NIFTY
    assert "NIFTY" in ctx.index_tables
    # 21 strikes available in the fixture (24000..25000 step 50), all within ATM ± 50
    assert ctx.index_tables["NIFTY"].chain.n_strikes == 21
    assert ctx.skipped_indices == []

    # F&O stock board built (M&M, RELIANCE from the sample), indices excluded
    assert ctx.stock_matrix is not None
    assert [s.name for s in ctx.stock_matrix.stock_refs] == ["M&M", "RELIANCE"]

    # tokens = index option tokens + spot + VIX + stock tokens; bridge subscribes them all
    cfg = get_index_config("NIFTY")
    assert cfg.spot_token in ctx.tokens
    assert VIX_TOKEN in ctx.tokens
    assert 519937 in ctx.tokens  # M&M spot
    assert ctx.bridge.tokens == ctx.tokens

    # engine + monitor wired; no hub -> no broadcaster
    assert ctx.engine.stock_matrix is ctx.stock_matrix
    assert ctx.monitor.bridge is ctx.bridge
    assert ctx.broadcaster is None


def test_bootstrap_with_hub_builds_broadcaster(tmp_path):
    ctx = bootstrap_capture(
        _settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        hub=FakeHub(),
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0}),
        clock=lambda: 1_753_070_400_000,
    )
    assert ctx.broadcaster is not None


def test_bootstrap_skips_index_without_spot(tmp_path):
    # No LTP for NIFTY -> spot 0 -> chain build fails -> skipped, but stocks still built.
    ctx = bootstrap_capture(
        _settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({}),  # empty
        clock=lambda: 1_753_070_400_000,
    )
    assert ctx.index_tables == {}
    assert ctx.skipped_indices == ["NIFTY"]
    assert ctx.stock_matrix is not None  # stocks don't need a spot quote


def test_bootstrap_raises_when_nothing_discovered(tmp_path):
    class EmptyStore:
        def get(self, exchange, trading_date, refresh=False):
            return []

    with pytest.raises(RuntimeError, match="no index chains and no stock board"):
        bootstrap_capture(
            _settings(tmp_path),
            access_token="tok",
            risk_free_rate=0.0691,
            instrument_store=EmptyStore(),
            quote_fn=_quote_fn({}),
            clock=lambda: 1_753_070_400_000,
        )



def test_bootstrap_propagates_rest_authentication_failure(tmp_path):
    from app.kite.errors import KiteAuthenticationError

    def rejected_quote(_symbols):
        raise KiteAuthenticationError("expired")

    with pytest.raises(KiteAuthenticationError):
        bootstrap_capture(
            _settings(tmp_path),
            access_token="expired",
            risk_free_rate=0.0691,
            instrument_store=FakeStore(),
            quote_fn=rejected_quote,
            clock=lambda: 1_753_070_400_000,
        )
