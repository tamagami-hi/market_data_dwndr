"""The 2026 exchange session structure, pinned at every boundary second.

Three segments, three different clocks — and the whole point of these tests is that a
single window for everything is wrong:

* equity **derivatives** (index and stock futures/options): continuous 09:15-15:40;
* F&O-eligible **cash** underlyings: continuous 09:15-15:15, then a closing call auction
  15:15-15:35, then closed;
* the capture **process** envelope: 09:10-15:40, which is neither of the above.

The failure modes these pin down all actually cost data or credibility:

* stopping derivative capture, or finalizing the archive, at 15:30 while index and stock
  derivatives are still trading for another ten minutes;
* counting the normal 15:15-15:40 cash close as 25 minutes of missing continuous data;
* excusing a genuine 15:30-15:40 derivative hole because the artifact was measured against
  a cash grid;
* letting the cash leg of a combined spot+futures frame go quiet at 15:15 and reporting the
  whole artifact — including its live futures legs — as a stale feed.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.capture import feed_health
from app.ops.automation import (
    ACTION_RUN_EOD,
    ACTION_STOP_CAPTURE,
    AutomationState,
    decide_automation,
)
from app.ops.calendar import TradingCalendar
from app.ops.completeness import reconcile
from app.ops.sessions import (
    PHASE_AUCTION,
    PHASE_BOOTSTRAP,
    PHASE_CLOSED,
    PHASE_OPEN,
    PHASE_PRE_OPEN,
    SESSION_EQUITY_CASH,
    SESSION_EQUITY_DERIV,
    build_session_registry,
)

IST = ZoneInfo("Asia/Kolkata")

# 2026-09-01 is a Tuesday: an ordinary trading day, and the next live session after the
# work these tests cover was written.
DAY = date(2026, 9, 1)


def at(hour: int, minute: int, second: int = 0) -> int:
    """Epoch ms for an IST wall-clock time on the reference trading day."""
    return int(
        datetime(DAY.year, DAY.month, DAY.day, hour, minute, second, tzinfo=IST).timestamp()
        * 1000
    )


def _settings(**overrides):
    """The deployed 2026 timing model, as the settings object exposes it."""
    base = {
        "timezone": "Asia/Kolkata",
        "market_holidays": [],
        # Broad envelope only — never the source of a segment's schedule.
        "market_open": "09:15",
        "market_close": "15:40",
        "capture_recovery_arm_delay_seconds": 300.0,
        # Equity derivatives: continuous to the 15:40 close.
        "equity_deriv_open": "09:15",
        "equity_deriv_close": "15:40",
        "equity_deriv_preopen_start": "09:00",
        "equity_deriv_preopen_end": "09:15",
        "equity_deriv_capture_preopen": False,
        "equity_deriv_enabled": True,
        "equity_deriv_cas_start": None,
        "equity_deriv_cas_end": None,
        # F&O cash underlyings: continuous to 15:15, then the closing auction to 15:35.
        "equity_cash_open": "09:15",
        "equity_cash_close": "15:15",
        "equity_cash_cas_start": "15:15",
        "equity_cash_cas_end": "15:35",
        "equity_cash_capture_auction": True,
        "equity_cash_preopen_start": "09:00",
        "equity_cash_preopen_end": "09:15",
        "equity_cash_capture_preopen": False,
        "equity_cash_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _registry(**overrides):
    """A registry wired the way capture bootstrap wires it.

    Artifacts take the WIDEST session among their legs — every artifact TickVault writes
    contains a derivative leg — while the legs themselves carry their own honest schedule.
    """
    return build_session_registry(
        _settings(**overrides),
        {
            "NIFTY": SESSION_EQUITY_DERIV,
            "STOCKS": SESSION_EQUITY_DERIV,
            "INDICES_FnO": SESSION_EQUITY_DERIV,
        },
        {
            "NIFTY:options": SESSION_EQUITY_DERIV,
            "NIFTY:spot": SESSION_EQUITY_CASH,
            "INDIA_VIX": SESSION_EQUITY_CASH,
            "STOCKS:futures": SESSION_EQUITY_DERIV,
            "STOCKS:cash": SESSION_EQUITY_CASH,
        },
    )


# --------------------------------------------------------------------------- #
# Derivatives: 09:15-15:40, including the final ten minutes.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("hh", "mm", "ss", "phase", "expected", "armed"),
    [
        # Before the process is even up. Pre-open runs 09:00-09:15, so the phase is
        # PRE_OPEN throughout — but with capture_pre_open off nothing is owed and no alarm
        # is armed. Process running != artifact expected to trade.
        (9, 9, 59, PHASE_PRE_OPEN, False, False),
        # The process starts at 09:10 — that does NOT make an artifact expected to trade.
        (9, 10, 0, PHASE_PRE_OPEN, False, False),
        (9, 14, 59, PHASE_PRE_OPEN, False, False),
        # Continuous session opens.
        (9, 15, 0, PHASE_OPEN, True, False),  # inside the 300 s arm delay
        (9, 20, 0, PHASE_OPEN, True, True),
        # 15:15 is the CASH close; derivatives carry straight through it.
        (15, 14, 59, PHASE_OPEN, True, True),
        (15, 15, 0, PHASE_OPEN, True, True),
        # 15:20 is a CASH auction stage, NOT an F&O close.
        (15, 19, 59, PHASE_OPEN, True, True),
        (15, 20, 0, PHASE_OPEN, True, True),
        # 15:30 is not the derivatives close either — this is the regression that mattered.
        (15, 29, 59, PHASE_OPEN, True, True),
        (15, 30, 0, PHASE_OPEN, True, True),
        (15, 34, 59, PHASE_OPEN, True, True),
        (15, 35, 0, PHASE_OPEN, True, True),
        (15, 39, 59, PHASE_OPEN, True, True),
        # 15:40 exactly: the close is exclusive, so nothing is owed at the boundary itself.
        (15, 40, 0, PHASE_CLOSED, False, False),
        (15, 40, 1, PHASE_CLOSED, False, False),
    ],
)
def test_derivatives_run_to_1540(hh, mm, ss, phase, expected, armed):
    registry = _registry()
    now = at(hh, mm, ss)
    assert registry.phase("NIFTY", now) == phase
    assert registry.is_capture_expected("NIFTY", now) is expected
    assert registry.is_stale_armed("NIFTY", now) is armed


def test_derivative_session_owes_6h25m_not_6h30m():
    deriv = _registry().sessions[SESSION_EQUITY_DERIV]
    # 09:15 -> 15:40 continuous = 6h25m. The 09:10 process start adds no owed seconds.
    assert deriv.scheduled_seconds() == 23_100
    assert deriv.auction_seconds() == 0


def test_1530_to_1540_is_scheduled_derivative_time():
    """The ten minutes that a 15:30 close would have silently written off."""
    registry = _registry()
    owed = registry.scheduled_seconds_between("NIFTY", at(15, 30), at(15, 40))
    assert owed == 600


# --------------------------------------------------------------------------- #
# Cash: continuous to 15:15, auction to 15:35, then closed.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("hh", "mm", "ss", "phase", "expected", "armed"),
    [
        (9, 9, 59, PHASE_PRE_OPEN, False, False),
        (9, 10, 0, PHASE_PRE_OPEN, False, False),
        (9, 14, 59, PHASE_PRE_OPEN, False, False),
        (9, 15, 0, PHASE_OPEN, True, False),
        (9, 20, 0, PHASE_OPEN, True, True),
        # Continuous matching ends at 15:15 and the auction begins in the same instant.
        (15, 14, 59, PHASE_OPEN, True, True),
        (15, 15, 0, PHASE_AUCTION, True, False),
        # Every CAS sub-stage is still the auction as far as capture is concerned.
        (15, 19, 59, PHASE_AUCTION, True, False),
        (15, 20, 0, PHASE_AUCTION, True, False),
        (15, 29, 59, PHASE_AUCTION, True, False),
        (15, 30, 0, PHASE_AUCTION, True, False),
        (15, 34, 59, PHASE_AUCTION, True, False),
        # Closing-price discovery complete.
        (15, 35, 0, PHASE_CLOSED, False, False),
        (15, 39, 59, PHASE_CLOSED, False, False),
        (15, 40, 0, PHASE_CLOSED, False, False),
        (15, 40, 1, PHASE_CLOSED, False, False),
    ],
)
def test_cash_continuous_ends_1515_then_auction(hh, mm, ss, phase, expected, armed):
    registry = _registry()
    now = at(hh, mm, ss)
    assert registry.phase("STOCKS:cash", now) == phase
    assert registry.is_capture_expected("STOCKS:cash", now) is expected
    assert registry.is_stale_armed("STOCKS:cash", now) is armed


def test_continuous_stale_rules_are_not_applied_to_cash_after_1515():
    """The explicit requirement: ordinary continuous-market staleness stops at 15:15."""
    registry = _registry()
    for hh, mm in ((15, 15), (15, 20), (15, 30), (15, 34), (15, 36), (15, 39)):
        assert registry.is_stale_armed("STOCKS:cash", at(hh, mm)) is False
    # ...while the derivative legs in the SAME physical frame stay armed throughout.
    for hh, mm in ((15, 15), (15, 20), (15, 30), (15, 34), (15, 36), (15, 39)):
        assert registry.is_stale_armed("STOCKS:futures", at(hh, mm)) is True


def test_auction_is_captured_but_is_not_continuous_owed_time():
    cash = _registry().sessions[SESSION_EQUITY_CASH]
    assert cash.scheduled_seconds() == 21_600  # 09:15-15:15
    assert cash.auction_seconds() == 1_200  # 15:15-15:35, tracked separately
    # The auction is NOT inside the completeness denominator...
    assert cash.scheduled_seconds_between(
        _registry().calendar, at(15, 15), at(15, 35)
    ) == 0
    # ...but frames are still expected, so closing-price discovery is persisted.
    local = datetime(DAY.year, DAY.month, DAY.day, 15, 25, tzinfo=IST)
    assert cash.is_capture_expected_at(local) is True
    assert cash.is_stale_armed_at(local) is False


def test_raising_the_cash_close_to_1540_is_not_how_auction_quiet_is_silenced():
    """Guard against the shortcut the model exists to avoid.

    Extending EQUITY_CASH_CLOSE to 15:40 also silences the alarm — by claiming 25 minutes
    of continuous cash matching that does not happen, and adding them to the denominator.
    """
    honest = _registry().sessions[SESSION_EQUITY_CASH]
    shortcut = _registry(
        equity_cash_close="15:40", equity_cash_cas_start=None, equity_cash_cas_end=None
    ).sessions[SESSION_EQUITY_CASH]
    assert honest.scheduled_seconds() == 21_600
    assert shortcut.scheduled_seconds() == 23_100
    # The shortcut arms continuous staleness through the auction; the honest model does not.
    local = datetime(DAY.year, DAY.month, DAY.day, 15, 25, tzinfo=IST)
    assert shortcut.is_stale_armed_at(local) is True
    assert honest.is_stale_armed_at(local) is False


def test_auction_start_must_not_precede_the_continuous_close():
    with pytest.raises(ValueError, match="closing auction cannot start"):
        _registry(equity_cash_cas_start="15:10")


# --------------------------------------------------------------------------- #
# Pre-open: process running != artifact expected to trade.
# --------------------------------------------------------------------------- #


def test_process_start_does_not_arm_preopen_staleness():
    registry = _registry()
    for hh, mm in ((9, 10), (9, 12), (9, 14)):
        now = at(hh, mm)
        # The phase is PRE_OPEN — the exchange call auction is genuinely running — but no
        # frame is owed and no alarm is armed, for either segment.
        assert registry.phase("NIFTY", now) == PHASE_PRE_OPEN
        assert registry.any_capture_expected(now) is False
        assert registry.any_stale_armed(now) is False


def test_bootstrap_is_the_phase_before_preopen():
    registry = _registry()
    assert registry.phase("NIFTY", at(8, 59, 59)) == PHASE_BOOTSTRAP
    assert registry.phase("NIFTY", at(9, 0, 0)) == PHASE_PRE_OPEN


def test_preopen_is_a_policy_not_an_assumption():
    """With capture_preopen on, 09:00-09:15 becomes owed; the alarm still stays disarmed."""
    registry = _registry(equity_deriv_capture_preopen=True)
    now = at(9, 5)
    assert registry.phase("NIFTY", now) == PHASE_PRE_OPEN
    assert registry.is_capture_expected("NIFTY", now) is True
    assert registry.is_stale_armed("NIFTY", now) is False
    assert registry.sessions[SESSION_EQUITY_DERIV].scheduled_seconds() == 23_100 + 900


# --------------------------------------------------------------------------- #
# Combined spot+futures artifacts must not be judged by one clock.
# --------------------------------------------------------------------------- #


def test_quiet_cash_leg_does_not_report_the_stock_artifact_as_stale():
    """A structurally quiet cash leg must not suppress genuinely live futures data."""
    registry = _registry()
    now = at(15, 32)  # cash in CAS, derivatives still in continuous matching
    stale_after_ms = 5_000

    ages = {
        "STOCKS": 400,  # artifact is fresh: the futures legs are ticking
        "STOCKS:cash": 17 * 60 * 1000,  # cash leg last printed before 15:15
        "STOCKS:futures": 300,
        "NIFTY:options": 250,
    }
    armed = {name: registry.is_stale_armed(name, now) for name in ages}

    assert armed["STOCKS:cash"] is False
    assert armed["STOCKS:futures"] is True

    # Session-blind: the quiet cash leg is reported as a feed fault.
    assert feed_health.stale_artifacts(ages, stale_after_ms) == ("STOCKS:cash",)
    assert (
        feed_health.classify(
            capture_expected=True,
            transport_age_ms=100,
            content_age_ms=200,
            stale_after_ms=stale_after_ms,
            artifact_ages_ms=ages,
        )
        == feed_health.HEALTH_ARTIFACT_STALE
    )

    # Session-aware: nothing is wrong, because nothing continuous is expected of that leg.
    assert feed_health.stale_artifacts(ages, stale_after_ms, armed=armed) == ()
    assert (
        feed_health.classify(
            capture_expected=True,
            transport_age_ms=100,
            content_age_ms=200,
            stale_after_ms=stale_after_ms,
            artifact_ages_ms=ages,
            artifact_stale_armed=armed,
        )
        == feed_health.HEALTH_HEALTHY
    )


def test_a_dead_futures_leg_after_1515_is_still_reported():
    """The other half of the same rule: exempting cash must not exempt derivatives."""
    registry = _registry()
    now = at(15, 32)
    ages = {"STOCKS:cash": 17 * 60 * 1000, "STOCKS:futures": 60_000}
    armed = {name: registry.is_stale_armed(name, now) for name in ages}

    assert feed_health.stale_artifacts(ages, 5_000, armed=armed) == ("STOCKS:futures",)


def test_stock_artifact_keeps_the_widest_session_so_futures_holes_stay_visible():
    """STOCKS is owed a frame until 15:40 even though its cash leg closed at 15:15."""
    registry = _registry()
    assert registry.is_capture_expected("STOCKS", at(15, 35)) is True
    assert registry.is_capture_expected("STOCKS", at(15, 39, 59)) is True
    assert registry.scheduled_seconds("STOCKS") == 23_100


# --------------------------------------------------------------------------- #
# Index spot / India VIX are audited apart from their derivatives.
# --------------------------------------------------------------------------- #


def test_index_spot_does_not_inherit_the_derivatives_close_by_default():
    registry = _registry()
    now = at(15, 30)
    # The option legs keep trading and stay armed...
    assert registry.phase("NIFTY:options", now) == PHASE_OPEN
    assert registry.is_stale_armed("NIFTY:options", now) is True
    # ...while the index VALUE follows its cash constituents into the auction.
    assert registry.phase("NIFTY:spot", now) == PHASE_AUCTION
    assert registry.is_stale_armed("NIFTY:spot", now) is False
    assert registry.phase("INDIA_VIX", now) == PHASE_AUCTION
    assert registry.is_stale_armed("INDIA_VIX", now) is False


def test_index_spot_session_is_one_config_edit():
    """If a live session proves index values tick to 15:40, this is the only change."""
    registry = _registry()
    registry.assign_contributor("NIFTY:spot", SESSION_EQUITY_DERIV)
    assert registry.is_stale_armed("NIFTY:spot", at(15, 30)) is True
    # And it never moves an artifact denominator, because spot is a leg, not an artifact.
    assert registry.scheduled_seconds("NIFTY") == 23_100


# --------------------------------------------------------------------------- #
# EOD must not finalize the day while derivatives are open.
# --------------------------------------------------------------------------- #


def _decide(hh, mm, ss=0, *, state=None, running=False, tail=120):
    return decide_automation(
        at(hh, mm, ss),
        state or AutomationState(),
        # The automation calendar is the PROCESS envelope: 09:10-15:40.
        calendar=TradingCalendar(market_open="09:10", market_close="15:40"),
        auth_poll_start="08:30",
        auth_poll_end="09:00",
        has_valid_session=True,
        is_capture_ready=True,
        is_capture_running=running,
        eod_tail_seconds=tail,
    )


@pytest.mark.parametrize(
    ("hh", "mm", "ss"),
    [
        (15, 14, 59),
        (15, 15, 0),
        (15, 19, 59),
        (15, 20, 0),
        (15, 29, 59),
        (15, 30, 0),
        (15, 34, 59),
        (15, 35, 0),
        (15, 39, 59),
    ],
)
def test_no_eod_while_derivatives_are_still_trading(hh, mm, ss):
    _state, actions = _decide(hh, mm, ss, running=True)
    assert ACTION_RUN_EOD not in actions
    assert ACTION_STOP_CAPTURE not in actions


def test_eod_waits_for_the_drain_tail_after_1540():
    state = AutomationState()
    # 15:40 exactly: capture stops, but the archive is NOT finalized — the last frames are
    # still in the writer queues.
    state, actions = _decide(15, 40, 0, state=state, running=True)
    assert actions == (ACTION_STOP_CAPTURE,)

    # Inside the tail, with capture already stopped: still no compression.
    state, actions = _decide(15, 41, 0, state=state, running=False)
    assert ACTION_RUN_EOD not in actions

    # Tail elapsed and the writers are drained: now the day may be finalized.
    state, actions = _decide(15, 42, 0, state=state, running=False)
    assert actions == (ACTION_STOP_CAPTURE, ACTION_RUN_EOD)


def test_eod_never_runs_while_capture_is_still_running():
    """Compression removes the raw .bin; racing a live writer would destroy frames."""
    _state, actions = _decide(16, 0, 0, running=True)
    assert ACTION_RUN_EOD not in actions
    _state, actions = _decide(16, 0, 0, running=False)
    assert ACTION_RUN_EOD in actions


def test_the_tail_is_a_drain_allowance_not_a_longer_session():
    """The safety tail must not add owed seconds anywhere."""
    registry = _registry()
    assert registry.is_capture_expected("NIFTY", at(15, 41)) is False
    assert registry.scheduled_seconds_between("NIFTY", at(15, 40), at(15, 42)) == 0
    assert registry.sessions[SESSION_EQUITY_DERIV].close_at.strftime("%H:%M") == "15:40"


# --------------------------------------------------------------------------- #
# Completeness accounting.
# --------------------------------------------------------------------------- #


def _frames(start_ms: int, end_ms: int) -> list[int]:
    return list(range(start_ms, end_ms, 1_000))


def test_a_normal_day_reports_no_missing_cash_data_for_1515_to_1540():
    """The explicit requirement: 15:15-15:40 is not 25 minutes of missing cash data."""
    registry = _registry()
    windows = registry.scheduled_windows_on("STOCKS:cash", DAY)
    # A perfect cash day: frames for every continuous second, and nothing after 15:15.
    captured = _frames(at(9, 15), at(15, 15))

    result = reconcile(windows, captured)

    assert result.scheduled_seconds == 21_600
    assert result.missing_seconds == 0
    assert result.data_loss_pct == 0.0
    assert result.reconciles


def test_auction_frames_are_neither_credited_nor_demanded():
    registry = _registry()
    windows = registry.scheduled_windows_on("STOCKS:cash", DAY)
    # Frames continue through the auction (closing-price discovery is real data).
    captured = _frames(at(9, 15), at(15, 35))

    result = reconcile(windows, captured)

    # The 1,200 auction seconds neither inflate the denominator nor the numerator.
    assert result.scheduled_seconds == 21_600
    assert result.captured_seconds == 21_600
    assert result.data_loss_pct == 0.0


def test_a_derivative_hole_from_1530_to_1540_is_real_loss():
    registry = _registry()
    windows = registry.scheduled_windows_on("NIFTY", DAY)
    captured = _frames(at(9, 15), at(15, 30))  # died at 15:30

    result = reconcile(windows, captured)

    assert result.scheduled_seconds == 23_100
    assert result.missing_seconds == 600
    assert result.gaps[-1].end_ms == at(15, 40)
    assert result.reconciles


def test_measuring_cash_against_the_derivatives_grid_invents_25_minutes_of_loss():
    """Why the reconciliation is per artifact rather than one shared window."""
    registry = _registry()
    captured = _frames(at(9, 15), at(15, 15))  # a perfect cash day

    wrong = reconcile(registry.scheduled_windows_on("NIFTY", DAY), captured)
    right = reconcile(registry.scheduled_windows_on("STOCKS:cash", DAY), captured)

    assert wrong.missing_seconds == 1_500  # 15:15 -> 15:40, none of it real
    assert right.missing_seconds == 0


def test_process_envelope_is_not_a_session():
    """09:10-15:40 is 23,400 s of process uptime, not 23,400 s of owed market data."""
    registry = _registry()
    envelope_seconds = (at(15, 40) - at(9, 10)) // 1000
    assert envelope_seconds == 23_400
    # Derivatives owe 23,100 of those; the 09:10-09:15 lead owes nothing.
    assert registry.scheduled_seconds("NIFTY") == 23_100
    assert registry.scheduled_seconds_between("NIFTY", at(9, 10), at(9, 15)) == 0



# --------------------------------------------------------------------------- #
# The engine wiring, end to end: legs are tracked and gated by their own session.
# --------------------------------------------------------------------------- #


def _index_engine(now_ms_value: int):
    """A one-index engine wired the way bootstrap wires it (leg map + arming gate)."""
    import numpy as np

    from app.capture.engine import CaptureEngine
    from app.chain.assembler import ROLE_OPTION, ROLE_SPOT, ROLE_VIX, OptionChain, Role
    from app.chain.table import IndexTable

    n = 2
    strikes = np.arange(2_200_000, 2_200_000 + n * 5_000, 5_000, dtype="<i8")
    call_tokens = np.array([11 + i for i in range(n)], dtype="<u8")
    put_tokens = np.array([21 + i for i in range(n)], dtype="<u8")
    token_map: dict[int, Role] = {}
    for i in range(n):
        token_map[int(call_tokens[i])] = Role(ROLE_OPTION, side="CE", index=i)
        token_map[int(put_tokens[i])] = Role(ROLE_OPTION, side="PE", index=i)
    token_map[256_265] = Role(ROLE_SPOT)
    token_map[264_969] = Role(ROLE_VIX)
    chain = OptionChain(
        "NIFTY", "2026-09-01", 2_250_000, 2_250_000,
        strikes, call_tokens, put_tokens, token_map,
    )
    table = IndexTable(chain, 0.0526, "2026-09-01")
    registry = _registry()
    contributor_tokens = {
        11: "NIFTY:options",
        12: "NIFTY:options",
        21: "NIFTY:options",
        22: "NIFTY:options",
        256_265: "NIFTY:spot",
        264_969: "INDIA_VIX",
    }
    engine = CaptureEngine(
        {"NIFTY": table},
        None,
        {},
        None,
        clock=lambda: now_ms_value,
        stale_armed_for=registry.is_stale_armed,
        contributor_tokens=contributor_tokens,
    )
    return engine


def test_engine_tracks_leg_freshness_separately_from_the_artifact():
    now = at(15, 30)
    engine = _index_engine(now)

    # An option print and a spot print, both applied at 09:14 — then only options keep
    # printing. This is the shape of a real 15:15+ session.
    engine.apply_ticks(
        [
            {"instrument_token": 11, "last_price": 100.0},
            {"instrument_token": 256_265, "last_price": 22_500.0},
        ],
        now=at(15, 14),
    )
    engine.apply_ticks([{"instrument_token": 12, "last_price": 101.0}], now=now)

    ages = engine.contributor_ages_ms(now)
    assert ages["NIFTY:options"] == 0
    assert ages["NIFTY:spot"] == 16 * 60 * 1000
    # The artifact itself is fresh, because the option legs are still printing.
    assert engine.artifact_ages_ms(now)["NIFTY"] == 0


def test_engine_does_not_report_a_quiet_spot_leg_as_stale_during_the_auction():
    now = at(15, 30)
    engine = _index_engine(now)
    engine.apply_ticks(
        [
            {"instrument_token": 11, "last_price": 100.0},
            {"instrument_token": 256_265, "last_price": 22_500.0},
        ],
        now=at(15, 14),
    )
    engine.apply_ticks([{"instrument_token": 12, "last_price": 101.0}], now=now)

    armed = engine.stale_armed_map(now)
    assert armed["NIFTY"] is True
    assert armed["NIFTY:options"] is True
    assert armed["NIFTY:spot"] is False
    assert armed["INDIA_VIX"] is False

    # No alarm: the only quiet legs are the ones in the cash closing auction.
    assert engine.stale_artifact_names(now) == ()
    assert engine.feed_health(now) == feed_health.HEALTH_HEALTHY


def test_engine_still_reports_a_quiet_option_leg_during_the_auction():
    """The exemption is per session, not a blanket amnesty after 15:15."""
    now = at(15, 30)
    engine = _index_engine(now)
    engine.apply_ticks(
        [
            {"instrument_token": 11, "last_price": 100.0},
            {"instrument_token": 256_265, "last_price": 22_500.0},
        ],
        now=at(15, 14),
    )
    # Nothing since 15:14 for the options either.
    assert "NIFTY:options" in engine.stale_artifact_names(now)


def test_engine_defaults_to_armed_without_a_session_resolver():
    """Constructing the engine directly keeps the previous single-session behaviour."""
    from app.capture.engine import CaptureEngine

    engine = CaptureEngine({}, None, {}, None, clock=lambda: at(15, 30))
    assert engine.stale_armed_map(at(15, 30)) == {}
    assert engine.contributor_ages_ms(at(15, 30)) == {}
