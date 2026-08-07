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



# --- restart-first recovery wiring --------------------------------------------


def _recovery_settings(tmp_path, **overrides):
    settings = _settings(tmp_path)
    settings.market_open = "09:10"  # as deployed: NSE trades continuously from 09:15
    settings.stats_dir = tmp_path / "_state" / "stats"
    settings.capture_stale_seconds = 5.0
    settings.capture_stale_exit_seconds = 60.0
    settings.capture_stale_exit_max_restarts = 3
    settings.capture_stale_recovery_confirm_seconds = 15.0
    settings.capture_recovery_arm_delay_seconds = 300.0
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _at(hhmm: str) -> int:
    """Epoch ms for 2026-08-06 at the given IST wall-clock time."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hour, minute = (int(part) for part in hhmm.split(":"))
    moment = datetime(2026, 8, 6, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata"))
    return int(moment.timestamp() * 1000)


def _bootstrap_for_recovery(tmp_path, **overrides):
    return bootstrap_capture(
        _recovery_settings(tmp_path, **overrides),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.5}),
        ticker_factory=lambda *a, **k: SimpleNamespace(),
        clock=lambda: _at("09:10"),
    )


def test_recovery_is_disarmed_until_the_exchange_is_actually_trading(tmp_path):
    """The 2026-08-04/05/06 trigger: capture starts at MARKET_OPEN, NSE trades at 09:15.

    Without this gate, restart-first recovery would exit the process every minute of
    every pre-open and spend the day's restart budget before trading began.
    """
    engine = _bootstrap_for_recovery(tmp_path).engine

    assert engine.recovery_armed(_at("09:00")) is False  # before MARKET_OPEN
    assert engine.recovery_armed(_at("09:12")) is False  # inside the arm grace period
    assert engine.recovery_armed(_at("09:15")) is True  # exchange is trading
    assert engine.recovery_armed(_at("16:00")) is False  # after MARKET_CLOSE


def test_recovery_settings_are_threaded_into_the_engine(tmp_path):
    engine = _bootstrap_for_recovery(tmp_path).engine

    assert engine.stale_exit_ms == 60_000
    assert engine.stale_recovery_confirm_ms == 15_000
    assert engine.escalation_limit == 3
    assert engine.escalations == 0
    assert engine.recovery_abandoned is False


def test_a_days_prior_escalations_are_carried_into_the_restart_budget(tmp_path):
    """The whole point of the ledger: the budget must survive the exits it counts."""
    from app.ops import stats_store

    stats_dir = tmp_path / "_state" / "stats"
    stats_dir.mkdir(parents=True)
    stats_store.record_escalation(stats_dir, "2026-08-06", _at("09:16"))
    stats_store.record_escalation(stats_dir, "2026-08-06", _at("09:18"))

    engine = _bootstrap_for_recovery(tmp_path).engine

    assert engine.escalations == 2  # one restart left before recovery is abandoned


def test_arm_delay_of_zero_arms_at_market_open(tmp_path):
    engine = _bootstrap_for_recovery(tmp_path, capture_recovery_arm_delay_seconds=0.0).engine

    assert engine.recovery_armed(_at("09:10")) is True
    assert engine.recovery_armed(_at("09:00")) is False



# --- subscription planning ----------------------------------------------------


def test_bootstrap_plans_the_subscription_universe_with_headroom(tmp_path):
    """The token count must be measured from the resolved instruments, not remembered."""
    settings = _recovery_settings(tmp_path)
    settings.broker_subscription_limit = 3_000
    settings.broker_subscription_safety_margin_pct = 10.0
    settings.broker_max_connections = 3

    ctx = bootstrap_capture(
        settings,
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.5}),
        ticker_factory=lambda *a, **k: SimpleNamespace(),
    )

    plan = ctx.subscription
    assert plan is not None
    # The plan's token set is exactly what the bridge was handed.
    assert list(plan.tokens) == ctx.tokens
    assert plan.token_count == len(ctx.tokens)
    # Headroom is known, and this universe comfortably fits one connection.
    assert plan.safe_limit == 2_700
    assert plan.remaining == 2_700 - plan.token_count
    assert plan.shard_count == 1
    assert plan.over_safe_threshold is False
    assert plan.exceeds_broker_capacity is False
    # Every capture domain is attributed.
    assert "NIFTY" in plan.breakdown
    assert "STOCKS" in plan.breakdown


def test_subscription_capacity_is_exposed_in_transport_telemetry(tmp_path):
    ctx = bootstrap_capture(
        _recovery_settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.5}),
        ticker_factory=lambda *a, **k: SimpleNamespace(),
    )

    transport = ctx.monitor.global_metrics()["transport"]

    assert transport["subscribed_tokens"] == len(ctx.tokens)
    assert transport["subscription_safe_limit"] == 2_700
    assert transport["subscription_shards"] == 1
    assert transport["subscription_over_threshold"] is False
    assert "queue_depth" in transport



def test_each_artifact_reports_its_own_phase_freshness_and_capture_state(tmp_path):
    """§20: a frozen index is invisible in the global signals, so artifacts answer for
    themselves — phase, whether a frame is owed, and their own last-update age."""
    ctx = bootstrap_capture(
        _recovery_settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.5}),
        ticker_factory=lambda *a, **k: SimpleNamespace(),
        clock=lambda: _at("12:00"),  # mid-session
    )

    entries = {entry["underlying"]: entry for entry in ctx.monitor.per_underlying()}
    assert "NIFTY" in entries and "STOCKS" in entries
    for entry in entries.values():
        assert entry["market_phase"] == "OPEN"
        assert entry["capture_active"] is True
        # Nothing has ticked yet, so every artifact reads as never-updated and stale.
        assert entry["artifact_age_ms"] is None
        assert entry["artifact_stale"] is True
        assert "last_frame_ms" in entry


def test_an_artifact_outside_its_session_reports_inactive_without_being_a_fault(tmp_path):
    ctx = bootstrap_capture(
        _recovery_settings(tmp_path),
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FakeStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.5}),
        ticker_factory=lambda *a, **k: SimpleNamespace(),
        clock=lambda: _at("16:30"),  # after the close
    )

    entries = ctx.monitor.per_underlying()
    assert entries
    for entry in entries:
        assert entry["market_phase"] == "CLOSED"
        assert entry["capture_active"] is False
    # And the global feed health is INACTIVE rather than alarming.
    assert ctx.monitor.global_metrics(entries)["feed_health"] == "INACTIVE"



# --- consolidated index-F&O domain --------------------------------------------


class FnoStore(FakeStore):
    """FakeStore plus index futures on NFO, so the index-F&O board can be derived."""

    def __init__(self):
        super().__init__()
        from tests.test_index_fno import _future

        self._by_exchange["NFO"] = [
            *self._by_exchange["NFO"],
            _future("NIFTY", "2026-08-27", 1001),
            _future("NIFTY", "2026-09-24", 1002),
            _future("NIFTY", "2026-10-29", 1003),
        ]


def _bootstrap_with_fno(tmp_path, **overrides):
    settings = _recovery_settings(tmp_path, **overrides)
    settings.indices_fno_dir = tmp_path / "INDICES_FnO"
    return bootstrap_capture(
        settings,
        access_token="tok",
        risk_free_rate=0.0691,
        instrument_store=FnoStore(),
        quote_fn=_quote_fn({"NSE:NIFTY 50": 24500.0, "NSE:INDIA VIX": 12.5}),
        ticker_factory=lambda *a, **k: SimpleNamespace(),
        clock=lambda: _at("12:00"),  # mid-session, so frames are scheduled
    )


def test_index_fno_domain_is_wired_as_a_third_capture_domain(tmp_path):
    ctx = _bootstrap_with_fno(tmp_path)

    assert ctx.engine.index_fno_matrix is not None
    assert ctx.engine.index_fno_writer is not None
    assert ctx.engine.index_fno_matrix.underlyings == ["NIFTY"]
    # It is a distinct artifact with its own session membership and freshness clock.
    assert "INDICES_FnO" in ctx.engine.artifact_names()
    assert "INDICES_FnO" in ctx.engine.artifact_ages_ms(0)


def test_index_fno_tokens_join_the_subscription_plan(tmp_path):
    ctx = _bootstrap_with_fno(tmp_path)

    plan = ctx.subscription
    assert "INDICES_FnO" in plan.breakdown
    # spot + up to 3 futures; the spot token is shared with the NIFTY option chain, so the
    # deduplicated universe grows only by the futures themselves.
    assert plan.breakdown["INDICES_FnO"] == len(ctx.engine.index_fno_matrix.tokens)
    assert all(token in plan.tokens for token in ctx.engine.index_fno_matrix.tokens)
    assert plan.over_safe_threshold is False


def test_index_fno_writes_its_own_file_leaving_the_others_untouched(tmp_path):
    ctx = _bootstrap_with_fno(tmp_path)
    ts = _at("12:00")
    matrix = ctx.engine.index_fno_matrix
    # Resolve the leg from the routing map rather than assuming it: the sample NFO dump
    # already carries a NIFTY future, so expiry order decides which slot 1001 lands in.
    leg = matrix.token_map[1001].leg
    ctx.engine.start_writers()
    try:
        ctx.engine.apply_ticks([{"instrument_token": 1001, "last_price": 24_600.0}], ts)
        snapshot = ctx.engine.capture_snapshot(ts)
    finally:
        ctx.engine.stop_writers()

    assert snapshot.index_fno_frame is not None
    assert snapshot.scheduled is True
    assert snapshot.written is True
    index_fno_file = tmp_path / "INDICES_FnO" / "2026-08-06.bin"
    assert index_fno_file.exists()
    # The pre-existing datasets still have their own files, unchanged in identity.
    assert (tmp_path / "INDICES" / "NIFTY" / "2026-08-06.bin").exists()

    from app.bin_codec.reader import IndexFnoBinReader

    with IndexFnoBinReader(index_fno_file) as reader:
        assert len(reader) == 1
        frame = reader.frame(0)
        assert getattr(frame, leg).scalars["ltp"][0] == 2_460_000


def test_index_fno_can_be_disabled_without_affecting_anything_else(tmp_path):
    ctx = _bootstrap_with_fno(tmp_path, indices_fno_enabled=False)

    assert ctx.engine.index_fno_matrix is None
    assert ctx.engine.index_fno_writer is None
    assert "INDICES_FnO" not in ctx.engine.artifact_names()
    assert "INDICES_FnO" not in ctx.subscription.breakdown
    # The existing domains are still present.
    assert "NIFTY" in ctx.engine.artifact_names()
    assert "STOCKS" in ctx.engine.artifact_names()



def test_index_fno_appears_as_just_another_artifact_in_the_status_payload(tmp_path):
    """§22: consumers iterate the artifact list, so a new domain needs no special case."""
    ctx = _bootstrap_with_fno(tmp_path)

    entries = ctx.monitor.per_underlying()
    names = [entry["underlying"] for entry in entries]

    assert names == ["NIFTY", "STOCKS", "INDICES_FnO"]
    fno = entries[-1]
    # It carries the same shape as every other artifact — no bespoke fields.
    assert set(fno) == set(entries[0])
    assert fno["market_phase"] in {"OPEN", "PRE_OPEN", "CLOSED", "BOOTSTRAP", "INACTIVE"}
    assert "frames_written" in fno and "artifact_age_ms" in fno
