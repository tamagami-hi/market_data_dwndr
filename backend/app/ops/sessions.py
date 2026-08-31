"""Market sessions: *when* persistence is valid, independent of what is persisted.

The capture engine used to reason about one global ``MARKET_OPEN``/``MARKET_CLOSE`` pair,
which conflated three different questions:

* is the process allowed to run?
* is this dataset expected to receive ticks right now?
* is an absence of ticks a fault worth restarting the process over?

Those came apart badly in the 2026-08-04/05/06 sessions: capture started at
``MARKET_OPEN`` (09:10 as deployed) while NSE's continuous session begins at 09:15, so the
first five minutes of every day looked like a dead feed. See
docs/30-live-capture/live-data-pipeline.md.

A :class:`MarketSession` answers those questions for one exchange session, and a
:class:`SessionRegistry` maps each captured **artifact** (a logical dataset — one index
file, the consolidated stock file, …) onto the session it belongs to. Writers persist
data; sessions own the schedule; artifacts merely reference a session. Nothing here
touches a binary format.

The 2026 exchange structure makes the per-segment split load-bearing rather than
decorative. Three different clocks apply to one capture process:

* equity **derivatives** (index/stock futures and options) trade continuously 09:15-15:40;
* the F&O-eligible **cash** underlyings stop continuous matching at 15:15 and then run a
  closing call auction to 15:35;
* the capture **process** may be alive 09:10-15:40, which is neither of the above.

So an artifact holding both a cash leg and a futures leg cannot be judged by one clock.
Artifacts take the *widest* session among their legs — the frame stream really is owed
until 15:40 — while :meth:`SessionRegistry.assign_contributor` gives each leg its own
schedule for freshness purposes. Without that split, the cash leg going quiet at 15:15
reports the whole artifact as a dead feed for the last 25 minutes of every session.

The schedule is deliberately derivable from configuration and a trading date **alone**,
with no reference to process uptime, because that is what makes data-loss accounting
honest: if the application was down from 09:15 to 09:27, those 720 scheduled seconds are
still expected frames (see :meth:`MarketSession.scheduled_seconds_between`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.ops.calendar import TradingCalendar

# Per-artifact lifecycle phases. A session is INACTIVE on non-trading days; BOOTSTRAP is
# the window where the process may prepare (instruments, chains, subscriptions) but no
# frame is expected; PRE_OPEN is the exchange pre-open call auction, captured only when
# the session's policy says so (§24 - pre-open is a policy, not an assumption).
PHASE_INACTIVE = "INACTIVE"
PHASE_BOOTSTRAP = "BOOTSTRAP"
PHASE_PRE_OPEN = "PRE_OPEN"
PHASE_OPEN = "OPEN"
# The closing call auction that follows the continuous session (NSE/BSE cash CAS,
# 15:15-15:35 as of the 2026 structure). Genuinely part of the trading day and worth
# recording, but NOT continuous matching: long stretches with no price change are the
# auction working as designed, so continuous-market staleness must not be armed and these
# seconds must not sit in the continuous-completeness denominator.
PHASE_AUCTION = "AUCTION"
PHASE_CLOSED = "CLOSED"

# Session names. Cash and derivatives do NOT share timings: as of the 2026 structure the
# equity-derivatives continuous session runs to 15:40 while the F&O-eligible cash
# underlyings stop continuous matching at 15:15 and then hold a closing auction to 15:35.
SESSION_EQUITY_DERIV = "equity_deriv"
SESSION_EQUITY_CASH = "equity_cash"


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class MarketSession:
    """One exchange session's schedule and capture policy.

    ``capture_pre_open`` decides whether the pre-open auction is part of this session's
    *scheduled* time. That single flag keeps pre-open out of the loss denominator for
    datasets that do not want it, without a second scheduling system.

    ``enabled`` distinguishes "intentionally not capturing" from "failed to capture"
    (§17.9): a disabled session schedules no seconds at all, so its silence can never be
    reported as data loss.
    """

    name: str
    open_at: time
    close_at: time
    pre_open_start: time | None = None
    pre_open_end: time | None = None
    capture_pre_open: bool = False
    enabled: bool = True
    # Grace period after ``open_at`` before absent ticks count as a fault. The exchange's
    # continuous session can begin after our configured open, and the first prints take a
    # moment to arrive.
    stale_arm_delay_s: float = 300.0
    # Closing call auction, which begins at or after ``close_at``. Kept as its own window
    # rather than folded into ``close_at`` because the two have different semantics: a
    # quiet second inside the auction is normal, a quiet second inside continuous
    # matching is a fault. Extending ``close_at`` to silence auction alarms would hide the
    # exchange structure instead of modelling it.
    cas_start: time | None = None
    cas_end: time | None = None
    # Whether auction frames are persisted at all. Closing-price discovery is real market
    # data, so the default is yes; it is still excluded from the continuous denominator.
    capture_auction: bool = True

    def __post_init__(self) -> None:
        if self.close_at <= self.open_at:
            raise ValueError(
                f"session {self.name}: close {self.close_at} must be after open {self.open_at}"
            )
        if self.capture_pre_open and not self.has_pre_open:
            raise ValueError(
                f"session {self.name}: capture_pre_open requires a pre-open window"
            )
        if self.has_pre_open and self.pre_open_end > self.open_at:  # type: ignore[operator]
            raise ValueError(
                f"session {self.name}: pre-open must end at or before open {self.open_at}"
            )
        if (self.cas_start is None) != (self.cas_end is None):
            raise ValueError(
                f"session {self.name}: the closing auction needs both a start and an end"
            )
        if self.has_auction:
            if self.cas_end <= self.cas_start:  # type: ignore[operator]
                raise ValueError(
                    f"session {self.name}: auction end {self.cas_end} must be after "
                    f"start {self.cas_start}"
                )
            if self.cas_start < self.close_at:  # type: ignore[operator]
                raise ValueError(
                    f"session {self.name}: the closing auction cannot start "
                    f"({self.cas_start}) before continuous close ({self.close_at})"
                )

    @property
    def has_pre_open(self) -> bool:
        return self.pre_open_start is not None and self.pre_open_end is not None

    @property
    def has_auction(self) -> bool:
        return self.cas_start is not None and self.cas_end is not None

    # -- phase ------------------------------------------------------------- #

    def phase_at(self, local: datetime) -> str:
        """Lifecycle phase for a local (exchange-timezone) datetime.

        The auction is tested before CLOSED because it starts *at* ``close_at``: the cash
        continuous session ending at 15:15 and the auction beginning at 15:15 are the same
        instant, and that instant belongs to the auction.
        """
        moment = local.time()
        if self.has_auction and self.cas_start <= moment < self.cas_end:  # type: ignore[operator]
            return PHASE_AUCTION
        if moment >= self.close_at:
            return PHASE_CLOSED
        if moment >= self.open_at:
            return PHASE_OPEN
        if self.has_pre_open and self.pre_open_start <= moment < self.pre_open_end:  # type: ignore[operator]
            return PHASE_PRE_OPEN
        return PHASE_BOOTSTRAP

    def is_capture_expected_at(self, local: datetime) -> bool:
        """True when a frame is expected for this session at ``local``.

        This is the gate that keeps scheduled inactivity out of the loss figure: outside
        it, an absent frame is *not expected data* rather than data loss.

        The auction is included when ``capture_auction`` so closing-price discovery is
        persisted — but :meth:`scheduled_intervals` still excludes it, so those seconds are
        recorded without ever becoming continuous-session loss.
        """
        if not self.enabled:
            return False
        phase = self.phase_at(local)
        if phase == PHASE_OPEN:
            return True
        if phase == PHASE_AUCTION:
            return self.capture_auction
        return phase == PHASE_PRE_OPEN and self.capture_pre_open

    def is_stale_armed_at(self, local: datetime) -> bool:
        """True when absent/frozen ticks are a fault, not merely a quiet market.

        Deliberately narrower than :meth:`is_capture_expected_at`: only the OPEN phase
        arms it. The pre-open call auction legitimately produces long silences, the
        **closing** call auction does too (unchanged values during CAS are the auction
        working, not a dead feed), and the first moments after open need a grace period —
        so none of those arm recovery escalation.
        """
        if not self.enabled or self.phase_at(local) != PHASE_OPEN:
            return False
        open_dt = local.replace(
            hour=self.open_at.hour,
            minute=self.open_at.minute,
            second=0,
            microsecond=0,
        )
        return (local - open_dt).total_seconds() >= self.stale_arm_delay_s

    # -- scheduled grid (uptime-independent) -------------------------------- #

    def scheduled_intervals(self) -> tuple[tuple[time, time], ...]:
        """The wall-clock intervals of **continuous** capture this session is scheduled.

        The closing auction is deliberately **not** here. These intervals are the
        completeness denominator, and a call auction has no per-second matching obligation:
        counting 15:15-15:35 as owed continuous cash seconds would report a normal auction
        as 20 minutes of missing market data. See :meth:`auction_intervals`.
        """
        if not self.enabled:
            return ()
        intervals: list[tuple[time, time]] = []
        if self.capture_pre_open and self.has_pre_open:
            intervals.append((self.pre_open_start, self.pre_open_end))  # type: ignore[arg-type]
        intervals.append((self.open_at, self.close_at))
        return tuple(intervals)

    def auction_intervals(self) -> tuple[tuple[time, time], ...]:
        """The closing-auction interval(s), tracked separately from continuous time."""
        if not self.enabled or not self.has_auction or not self.capture_auction:
            return ()
        return ((self.cas_start, self.cas_end),)  # type: ignore[return-value]

    def auction_seconds(self) -> int:
        """Wall-clock seconds this session spends in its closing auction."""
        return sum(
            _seconds_between(start, end) for start, end in self.auction_intervals()
        )

    def auction_windows_on(
        self, calendar: TradingCalendar, trading_date: date
    ) -> tuple[tuple[int, int], ...]:
        """Absolute ``[start_ms, end_ms)`` auction windows on ``trading_date``."""
        if not calendar.is_trading_day(_at(trading_date, time(12, 0), calendar)):
            return ()
        return tuple(
            (_at(trading_date, start, calendar), _at(trading_date, end, calendar))
            for start, end in self.auction_intervals()
        )

    def scheduled_seconds(self) -> int:
        """Total continuous seconds this session is scheduled to capture on a trading day.

        Derived from configuration only — never from what the process observed — so it is
        the honest denominator for completeness even if capture never ran. Excludes the
        closing auction (see :meth:`auction_seconds`).
        """
        return sum(
            _seconds_between(start, end) for start, end in self.scheduled_intervals()
        )

    def scheduled_windows_on(
        self, calendar: TradingCalendar, trading_date: date
    ) -> tuple[tuple[int, int], ...]:
        """Absolute ``[start_ms, end_ms)`` windows this session owes on ``trading_date``.

        The archive-truth reconciliation needs the owed grid as wall-clock milliseconds,
        not as ``time`` objects, so it can be intersected with the frame timestamps that
        actually reached disk. Returns ``()`` on a non-trading day or a disabled session.
        """
        if not self.enabled:
            return ()
        if not calendar.is_trading_day(_at(trading_date, time(12, 0), calendar)):
            return ()
        return tuple(
            (_at(trading_date, start, calendar), _at(trading_date, end, calendar))
            for start, end in self.scheduled_intervals()
        )

    def scheduled_seconds_between(
        self, calendar: TradingCalendar, start_ms: int, end_ms: int
    ) -> int:
        """Scheduled capture seconds inside ``[start_ms, end_ms)``.

        The core of downtime-aware loss accounting: hand it the last persisted frame and
        the moment capture came back and it returns how many market seconds were owed in
        between, whether or not anything was running to observe them. Non-trading days
        contribute nothing.
        """
        if end_ms <= start_ms or not self.enabled:
            return 0
        total = 0
        start_local = calendar.local_dt(start_ms)
        end_local = calendar.local_dt(end_ms)
        day = start_local.date()
        while day <= end_local.date():
            day_ms = int(
                datetime.combine(day, time(12, 0), tzinfo=calendar.tz).timestamp() * 1000
            )
            if calendar.is_trading_day(day_ms):
                for window_start, window_end in self.scheduled_intervals():
                    total += _overlap_seconds(
                        _at(day, window_start, calendar),
                        _at(day, window_end, calendar),
                        start_ms,
                        end_ms,
                    )
            day += timedelta(days=1)
        return total

    def scheduled_seconds_elapsed(self, calendar: TradingCalendar, now_ms: int) -> int:
        """Scheduled seconds owed from the start of today's session up to ``now``."""
        local = calendar.local_dt(now_ms)
        day_start = _at(local.date(), time(0, 0), calendar)
        return self.scheduled_seconds_between(calendar, day_start, now_ms)


def _seconds_between(start: time, end: time) -> int:
    return int(
        (
            datetime.combine(date(2000, 1, 1), end)
            - datetime.combine(date(2000, 1, 1), start)
        ).total_seconds()
    )


def _at(day: date, moment: time, calendar: TradingCalendar) -> int:
    return int(datetime.combine(day, moment, tzinfo=calendar.tz).timestamp() * 1000)


def _overlap_seconds(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    overlap_ms = min(a_end, b_end) - max(a_start, b_start)
    return max(0, overlap_ms) // 1000


class SessionRegistry:
    """Maps captured artifacts — and their individual legs — onto market sessions.

    An *artifact* is one logical dataset written as one physical frame stream (``NIFTY``,
    ``STOCKS``, ``INDICES_FnO``). Artifacts reference a session by name instead of carrying
    duplicated trading times, so several artifacts sharing a session share one piece of
    configuration (§4).

    A *contributor* is one **leg inside** an artifact: ``STOCKS`` carries both cash spot and
    stock futures, whose sessions genuinely differ (15:15 vs 15:40). An artifact that
    contains a derivative leg must keep the *widest* session, because its frame stream is
    still owed at 15:39 — but freshness has to be judged per leg, or the cash leg going
    quiet at 15:15 would mark the whole artifact stale and suppress live futures data.
    Contributors are therefore kept in a separate map: they take part in freshness
    decisions, never in the artifact-level scheduled/owed grid.
    """

    def __init__(
        self,
        calendar: TradingCalendar,
        sessions: dict[str, MarketSession],
        artifact_sessions: dict[str, str] | None = None,
        default_session: str = SESSION_EQUITY_DERIV,
        contributor_sessions: dict[str, str] | None = None,
    ) -> None:
        if default_session not in sessions:
            raise ValueError(f"default session {default_session!r} is not configured")
        self.calendar = calendar
        self.sessions = dict(sessions)
        self._artifact_sessions = dict(artifact_sessions or {})
        self._contributor_sessions = dict(contributor_sessions or {})
        self.default_session = default_session

    def session_for(self, artifact: str) -> MarketSession:
        name = self._artifact_sessions.get(artifact) or self._contributor_sessions.get(
            artifact, self.default_session
        )
        return self.sessions.get(name, self.sessions[self.default_session])

    def assign(self, artifact: str, session_name: str) -> None:
        if session_name not in self.sessions:
            raise ValueError(f"unknown session {session_name!r}")
        self._artifact_sessions[artifact] = session_name

    def assign_contributor(self, contributor: str, session_name: str) -> None:
        """Bind one leg of an artifact to a session, for freshness purposes only."""
        if session_name not in self.sessions:
            raise ValueError(f"unknown session {session_name!r}")
        self._contributor_sessions[contributor] = session_name

    # -- per-artifact questions -------------------------------------------- #

    def phase(self, artifact: str, now_ms: int) -> str:
        if not self.calendar.is_trading_day(now_ms):
            return PHASE_INACTIVE
        return self.session_for(artifact).phase_at(self.calendar.local_dt(now_ms))

    def is_capture_expected(self, artifact: str, now_ms: int) -> bool:
        if not self.calendar.is_trading_day(now_ms):
            return False
        return self.session_for(artifact).is_capture_expected_at(
            self.calendar.local_dt(now_ms)
        )

    def is_stale_armed(self, artifact: str, now_ms: int) -> bool:
        if not self.calendar.is_trading_day(now_ms):
            return False
        return self.session_for(artifact).is_stale_armed_at(
            self.calendar.local_dt(now_ms)
        )

    def is_auction(self, artifact: str, now_ms: int) -> bool:
        """True when this artifact/leg is in its closing call auction right now."""
        return self.phase(artifact, now_ms) == PHASE_AUCTION

    def any_capture_expected(self, now_ms: int) -> bool:
        """True when at least one artifact is scheduled to receive data right now."""
        return any(
            self.is_capture_expected(artifact, now_ms) for artifact in self.artifacts()
        ) or (
            not self._artifact_sessions
            and self.is_capture_expected("__default__", now_ms)
        )

    def any_stale_armed(self, now_ms: int) -> bool:
        """True when a dead feed is a fault for at least one artifact (§16)."""
        if self._artifact_sessions:
            return any(
                self.is_stale_armed(artifact, now_ms) for artifact in self.artifacts()
            )
        return self.is_stale_armed("__default__", now_ms)

    def artifacts(self) -> tuple[str, ...]:
        return tuple(self._artifact_sessions)

    def contributors(self) -> tuple[str, ...]:
        """Named legs inside artifacts, which are judged for freshness on their own."""
        return tuple(self._contributor_sessions)

    def scheduled_seconds(self, artifact: str) -> int:
        return self.session_for(artifact).scheduled_seconds()

    def scheduled_seconds_elapsed(self, artifact: str, now_ms: int) -> int:
        return self.session_for(artifact).scheduled_seconds_elapsed(
            self.calendar, now_ms
        )

    def scheduled_seconds_between(self, artifact: str, start_ms: int, end_ms: int) -> int:
        return self.session_for(artifact).scheduled_seconds_between(
            self.calendar, start_ms, end_ms
        )

    def scheduled_windows_on(self, artifact: str, trading_date) -> tuple[tuple[int, int], ...]:
        """Absolute owed windows for ``artifact`` on ``trading_date`` (a ``date``)."""
        return self.session_for(artifact).scheduled_windows_on(self.calendar, trading_date)

    def auction_windows_on(self, artifact: str, trading_date) -> tuple[tuple[int, int], ...]:
        """Absolute closing-auction windows for ``artifact`` on ``trading_date``."""
        return self.session_for(artifact).auction_windows_on(self.calendar, trading_date)


def build_session_registry(
    settings,
    artifacts: dict[str, str] | None = None,
    contributors: dict[str, str] | None = None,
) -> SessionRegistry:
    """Assemble the registry from settings.

    Each session's timings come from its own ``EQUITY_DERIV_*`` / ``EQUITY_CASH_*`` block.
    ``MARKET_OPEN``/``MARKET_CLOSE`` remain only as the broad process/UI envelope and as a
    last-resort fallback for a segment whose own setting has been explicitly blanked — they
    must not be the normal source of a segment's schedule, because that is how the cash
    session silently inherited the derivatives close.
    """
    calendar = TradingCalendar(
        holidays=set(getattr(settings, "market_holidays", [])),
        timezone_name=getattr(settings, "timezone", "Asia/Kolkata"),
        market_open=settings.market_open,
        market_close=settings.market_close,
    )
    arm_delay = float(getattr(settings, "capture_recovery_arm_delay_seconds", 300.0))

    def _session(name: str, prefix: str) -> MarketSession:
        pre_start = getattr(settings, f"{prefix}_preopen_start", None)
        pre_end = getattr(settings, f"{prefix}_preopen_end", None)
        cas_start = getattr(settings, f"{prefix}_cas_start", None)
        cas_end = getattr(settings, f"{prefix}_cas_end", None)
        return MarketSession(
            name=name,
            open_at=parse_hhmm(getattr(settings, f"{prefix}_open", None) or settings.market_open),
            close_at=parse_hhmm(
                getattr(settings, f"{prefix}_close", None) or settings.market_close
            ),
            pre_open_start=parse_hhmm(pre_start) if pre_start else None,
            pre_open_end=parse_hhmm(pre_end) if pre_end else None,
            capture_pre_open=bool(getattr(settings, f"{prefix}_capture_preopen", False)),
            enabled=bool(getattr(settings, f"{prefix}_enabled", True)),
            stale_arm_delay_s=arm_delay,
            cas_start=parse_hhmm(cas_start) if cas_start else None,
            cas_end=parse_hhmm(cas_end) if cas_end else None,
            capture_auction=bool(getattr(settings, f"{prefix}_capture_auction", True)),
        )

    sessions = {
        SESSION_EQUITY_DERIV: _session(SESSION_EQUITY_DERIV, "equity_deriv"),
        SESSION_EQUITY_CASH: _session(SESSION_EQUITY_CASH, "equity_cash"),
    }
    return SessionRegistry(
        calendar,
        sessions,
        artifacts,
        SESSION_EQUITY_DERIV,
        contributor_sessions=contributors,
    )


__all__ = [
    "PHASE_AUCTION",
    "PHASE_BOOTSTRAP",
    "PHASE_CLOSED",
    "PHASE_INACTIVE",
    "PHASE_OPEN",
    "PHASE_PRE_OPEN",
    "SESSION_EQUITY_CASH",
    "SESSION_EQUITY_DERIV",
    "MarketSession",
    "SessionRegistry",
    "build_session_registry",
    "parse_hhmm",
]
