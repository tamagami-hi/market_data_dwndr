"""Live capture bootstrap — wire the whole pipeline for a trading day.

Given a logged-in session, this assembles the runnable capture:

    instrument dumps ──▶ index option chains (ATM ± 50, seeded by an LTP quote)
                    └──▶ F&O stock board (spot + 3 futures)
        ──▶ IndexTable(s) + StockMatrix ──▶ per-file writer threads
        ──▶ CaptureEngine + CaptureMonitor + (optional) Broadcaster
        ──▶ TickerBridge subscribing every token

``bootstrap_capture`` is dependency-injected (instrument store / quote fn / ticker
factory / hub) so it is unit-testable without the network; ``run_capture`` drives the
live loop until a stop event fires.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.capture.engine import (
    CaptureEngine,
    build_index_fno_writer,
    build_index_writer,
    build_stock_writer,
)
from app.capture.monitor import CaptureMonitor
from app.capture.subscription import plan_subscriptions
from app.chain.assembler import ROLE_SPOT, ROLE_VIX, build_option_chain
from app.chain.config import VIX_SYMBOL, get_index_config
from app.chain.table import IndexTable
from app.index_fno.board import discover_index_fno_board
from app.index_fno.matrix import IndexFnoMatrix
from app.kite.errors import KiteAuthenticationError, is_authentication_error
from app.kite.instruments import InstrumentStore
from app.kite.ticker import CONNECT_TIMEOUT_S, TickerBridge, TickerConnectFailed
from app.ops.calendar import TradingCalendar
from app.ops.sessions import (
    SESSION_EQUITY_CASH,
    SESSION_EQUITY_DERIV,
    build_session_registry,
)
from app.ops.stats_store import load_capture_snapshot, load_escalations, record_escalation
from app.session import now_ms
from app.stocks.board import discover_fno_board
from app.stocks.matrix import StockMatrix

logger = logging.getLogger(__name__)


@dataclass
class CaptureContext:
    """Everything needed to run (and observe) a capture session."""

    engine: CaptureEngine
    bridge: TickerBridge
    monitor: CaptureMonitor
    index_tables: dict[str, IndexTable]
    stock_matrix: StockMatrix | None
    tokens: list[int]
    trading_date: str
    broadcaster: object | None = None
    skipped_indices: list[str] = field(default_factory=list)
    subscription: object | None = None  # SubscriptionPlan
    bus: object | None = None  # BusPublisher | NullBusPublisher


def _default_instrument_store(settings, access_token: str) -> InstrumentStore:
    from app.kite.auth import auth_header
    from app.kite.instruments import default_http_fetcher

    fetcher = default_http_fetcher(headers=auth_header(settings.kite_api_key, access_token))
    return InstrumentStore(settings.instruments_dir, fetcher)


def bootstrap_capture(
    settings,
    access_token: str,
    risk_free_rate: float,
    *,
    hub=None,
    instrument_store: InstrumentStore | None = None,
    quote_fn=None,
    ticker_factory=None,
    token_provider=None,
    token_refresher=None,
    clock=now_ms,
) -> CaptureContext:
    """Assemble a :class:`CaptureContext` for today's session."""
    calendar = TradingCalendar(
        holidays=set(getattr(settings, "market_holidays", [])),
        timezone_name=settings.timezone,
        market_open=settings.market_open,
        market_close=settings.market_close,
    )
    trading_date = calendar.trading_date(clock())

    if instrument_store is None:
        instrument_store = _default_instrument_store(settings, access_token)
    if quote_fn is None:
        from app.kite.quotes import default_quote_fn

        quote_fn = default_quote_fn(settings, access_token)

    # --- seed spot prices (LTP) for the configured indices + VIX ---
    configs = {name: get_index_config(name) for name in settings.indices}
    spot_symbols = [cfg.spot_symbol for cfg in configs.values()]
    try:
        ltps = quote_fn([*spot_symbols, VIX_SYMBOL])
    except Exception as exc:  # noqa: BLE001 - a quote failure shouldn't abort stocks
        if is_authentication_error(exc):
            raise KiteAuthenticationError("Kite access token was rejected") from exc
        logger.warning("LTP quote failed; index chains may be skipped: %s", exc)
        ltps = {}

    # --- index option chains (L1) ---
    index_tables: dict[str, IndexTable] = {}
    index_writers: dict = {}
    skipped: list[str] = []
    for name, cfg in configs.items():
        try:
            instruments = instrument_store.get(cfg.options_exchange, trading_date)
            spot = ltps.get(cfg.spot_symbol, 0.0)
            chain = build_option_chain(instruments, cfg, spot=spot, today=trading_date)
            table = IndexTable(chain, risk_free_rate, trading_date)
            index_tables[name] = table
            path = settings.indices_dir / name / f"{trading_date}.bin"
            index_writers[name] = build_index_writer(table, path)
            logger.info("chain ready: %s %s (%d strikes, spot %.2f)",
                        name, chain.expiry, chain.n_strikes, spot)
        except Exception as exc:  # noqa: BLE001 - skip a bad index, keep the rest
            if is_authentication_error(exc):
                raise KiteAuthenticationError("Kite access token was rejected") from exc
            skipped.append(name)
            logger.warning("skipping index %s: %s", name, exc)

    # --- F&O stock board (L5) ---
    stock_matrix: StockMatrix | None = None
    stock_writer = None
    try:
        board = discover_fno_board(instrument_store, trading_date, settings.stock_universe)
        if board:
            stock_matrix = StockMatrix(board, risk_free_rate, trading_date)
            stock_path = settings.stocks_dir / f"{trading_date}.bin"
            stock_writer = build_stock_writer(stock_matrix, stock_path)
            logger.info("stock board ready: %d F&O stocks", len(board))
    except Exception as exc:  # noqa: BLE001
        if is_authentication_error(exc):
            raise KiteAuthenticationError("Kite access token was rejected") from exc
        logger.warning("stock board discovery failed: %s", exc)

    # --- consolidated index-F&O domain ------------------------------------------- #
    # Index futures + each index's spot, all indices on one grid in one file. Kept apart
    # from the per-index option files and from the stock file: separate domains with
    # independently evolvable schemas, and no change to any existing binary contract.
    # Coverage follows settings.indices, so extending the supported index set is a config
    # change rather than a change here.
    index_fno_matrix = None
    index_fno_writer = None
    if getattr(settings, "indices_fno_enabled", True):
        try:
            fno_board = discover_index_fno_board(
                instrument_store, trading_date, list(settings.indices)
            )
            if fno_board:
                index_fno_matrix = IndexFnoMatrix(fno_board, risk_free_rate, trading_date)
                index_fno_path = settings.indices_fno_dir / f"{trading_date}.bin"
                index_fno_writer = build_index_fno_writer(index_fno_matrix, index_fno_path)
                logger.info(
                    "index-F&O board ready: %d indices, %d futures (%s)",
                    len(fno_board),
                    sum(len(entry.futures) for entry in fno_board),
                    ", ".join(
                        f"{entry.underlying}x{len(entry.futures)}" for entry in fno_board
                    ),
                )
            else:
                logger.warning(
                    "index-F&O enabled but no index futures were discovered for %s",
                    ", ".join(settings.indices),
                )
        except Exception as exc:  # noqa: BLE001
            if is_authentication_error(exc):
                raise KiteAuthenticationError("Kite access token was rejected") from exc
            logger.warning("index-F&O board discovery failed: %s", exc)

    if not index_tables and stock_matrix is None:
        raise RuntimeError("bootstrap produced no index chains and no stock board")

    # --- restart-first recovery wiring --------------------------------------------- #
    # Scheduling now comes from the artifact's market session rather than a single global
    # window (app/ops/sessions.py). Three distinct gates fall out of it:
    #
    #   capture_expected -- is a frame owed at all? Outside the session, or with the
    #                       session disabled, an absent frame is not data loss.
    #   stale_armed      -- is absent data a FAULT? Narrower still: the pre-open call
    #                       auction is legitimately silent, the CLOSING call auction is
    #                       too, and the first minutes after open need a grace period.
    #                       Without this the process would exit every minute of pre-open.
    #   stale_armed_for  -- the same question per artifact AND per leg. Needed because a
    #                       single physical frame spans two sessions (below).
    #
    # ARTIFACT session = the WIDEST session among the legs the artifact contains. Every
    # artifact TickVault writes is a combined frame: the index files hold spot + VIX +
    # option strikes, STOCKS holds cash spot + three futures legs, INDICES_FnO is wholly
    # derivative. All of them carry a derivative leg that trades to 15:40, so all of them
    # are owed a frame until 15:40 and must be reconciled against the derivatives grid —
    # narrowing STOCKS to the cash session would silently excuse a real 15:30-15:40
    # futures hole.
    artifact_sessions = {name: SESSION_EQUITY_DERIV for name in index_tables}
    if stock_matrix is not None:
        artifact_sessions["STOCKS"] = SESSION_EQUITY_DERIV
    if index_fno_matrix is not None:
        artifact_sessions["INDICES_FnO"] = SESSION_EQUITY_DERIV

    # LEG (contributor) sessions = the honest per-slice schedule, used for freshness only,
    # never for the owed grid. This is what stops the cash spot leg going quiet at 15:15
    # from marking the whole STOCKS artifact stale and suppressing live futures data.
    spot_session = (
        SESSION_EQUITY_CASH
        if getattr(settings, "index_spot_session", "equity_cash") == SESSION_EQUITY_CASH
        else SESSION_EQUITY_DERIV
    )
    contributor_sessions: dict[str, str] = {}
    contributor_tokens: dict[int, str] = {}
    for name, table in index_tables.items():
        for token, role in table.chain.token_map.items():
            if role.kind == ROLE_SPOT:
                leg = f"{name}:spot"
                contributor_sessions[leg] = spot_session
            elif role.kind == ROLE_VIX:
                # India VIX is a volatility INDEX derived from NIFTY option prices, not a
                # traded contract. It is fanned out to every index table, so it gets one
                # shared leg name rather than one per index.
                leg = "INDIA_VIX"
                contributor_sessions[leg] = spot_session
            else:
                leg = f"{name}:options"
                contributor_sessions[leg] = SESSION_EQUITY_DERIV
            contributor_tokens[token] = leg
    if stock_matrix is not None:
        for token, stock_role in stock_matrix.token_map.items():
            # LEG_SLOTS = ("spot", "fut_current", "fut_mid", "fut_far"): the spot leg is
            # the F&O-eligible CASH underlying (continuous to 15:15, then CAS to 15:35);
            # the three futures legs stay in the derivatives session to 15:40.
            is_cash = stock_role.leg == "spot"
            leg = "STOCKS:cash" if is_cash else "STOCKS:futures"
            contributor_sessions[leg] = (
                SESSION_EQUITY_CASH if is_cash else SESSION_EQUITY_DERIV
            )
            contributor_tokens[token] = leg

    session_registry = build_session_registry(
        settings, artifact_sessions, contributor_sessions
    )
    deriv = session_registry.session_for(next(iter(artifact_sessions), "STOCKS"))
    cash = session_registry.sessions[SESSION_EQUITY_CASH]
    logger.info(
        "session schedule: %s %s-%s (pre-open %s, %d scheduled seconds/day)",
        deriv.name,
        deriv.open_at.strftime("%H:%M"),
        deriv.close_at.strftime("%H:%M"),
        "captured" if deriv.capture_pre_open else "not captured",
        deriv.scheduled_seconds(),
    )
    logger.info(
        "session schedule: %s %s-%s continuous (%d scheduled seconds/day)%s",
        cash.name,
        cash.open_at.strftime("%H:%M"),
        cash.close_at.strftime("%H:%M"),
        cash.scheduled_seconds(),
        (
            f" + closing auction {cash.cas_start:%H:%M}-{cash.cas_end:%H:%M} "
            f"({cash.auction_seconds()}s, captured but NOT in the continuous denominator)"
            if cash.has_auction
            else " (no closing auction configured)"
        ),
    )
    if contributor_sessions:
        logger.info(
            "leg sessions: %s",
            ", ".join(
                f"{leg}->{contributor_sessions[leg]}"
                for leg in sorted(contributor_sessions)
            ),
        )

    # The frame baseline comes from the SESSION (when frames are owed), not from the
    # capture window (when the process runs). Those differ by design — the process starts
    # early to get its socket up — and using the wider window would understate day-progress
    # and quietly disagree with the scheduled loss figures.
    #
    # An explicit EXPECTED_FRAMES_PER_SESSION override wins, because an operator setting it
    # to correct the metric must actually see an effect (it used to be unreachable behind
    # an ``or`` short-circuit). A *disabled* session owes nothing, so it must never fall
    # back to a full-day baseline against zero captures — that reported 100% loss for an
    # artifact deliberately switched off.
    session_frames = session_registry.scheduled_seconds(
        next(iter(artifact_sessions), "STOCKS")
    )
    override = getattr(settings, "expected_frames_override", None)
    if override:
        expected_frames = int(override)
        logger.info("frame baseline overridden by EXPECTED_FRAMES_PER_SESSION=%d", override)
    elif session_frames:
        expected_frames = session_frames
    elif not deriv.enabled:
        expected_frames = 0  # nothing owed: a disabled session cannot be "missing" data
    else:
        expected_frames = getattr(settings, "expected_frames_per_session", 0)

    if not getattr(settings, "market_holidays", ()):  # noqa: SIM102 - explicit is clearer
        logger.warning(
            "MARKET_HOLIDAYS is empty: any weekday exchange holiday will be treated as a "
            "full trading day (23,100 owed seconds, zero captures, 100%% reported loss) "
            "and capture/EOD will still run on it"
        )

    escalations_before = 0
    stats_root = getattr(settings, "stats_dir", None)
    if stats_root is not None:
        try:
            escalations_before = int(
                load_escalations(stats_root, trading_date).get("count") or 0
            )
        except Exception as exc:  # noqa: BLE001 - ledger is advisory, never block start
            logger.warning("could not read the escalation ledger: %s", exc)
        if escalations_before:
            logger.warning(
                "this trading date has already escalated %d time(s) for a dead feed",
                escalations_before,
            )

    def _record_escalation() -> int:
        if stats_root is None:
            return 0
        return record_escalation(stats_root, trading_date, clock())

    engine = CaptureEngine(
        index_tables,
        stock_matrix,
        index_writers,
        stock_writer,
        clock=clock,
        index_fno_matrix=index_fno_matrix,
        index_fno_writer=index_fno_writer,
        stale_after_ms=int(round(getattr(settings, "capture_stale_seconds", 5.0) * 1000)),
        suppress_stale_writes=bool(getattr(settings, "capture_suppress_stale_writes", True)),
        stale_exit_ms=int(round(getattr(settings, "capture_stale_exit_seconds", 60.0) * 1000)),
        stale_recovery_confirm_ms=int(
            round(getattr(settings, "capture_stale_recovery_confirm_seconds", 15.0) * 1000)
        ),
        reconnect_after_ms=int(
            round(getattr(settings, "capture_reconnect_after_seconds", 10.0) * 1000)
        ),
        reconnect_limit_per_hour=getattr(settings, "capture_reconnect_max_per_hour", 6),
        recovery_armed=session_registry.any_stale_armed,
        capture_expected=session_registry.any_capture_expected,
        stale_armed_for=session_registry.is_stale_armed,
        contributor_tokens=contributor_tokens,
        token_refresher=token_refresher,
        escalation_recorder=_record_escalation,
        escalations_before=escalations_before,
        escalation_limit=getattr(settings, "capture_stale_exit_max_restarts", 3),
    )
    # --- resume a mid-session restart ------------------------------------------ #
    # The .bin files append, so data written before the restart is intact; the counters
    # are not. Seed them from disk (authoritative) plus the last persisted monitor
    # snapshot (for counters that leave no trace in the files) so the dashboard shows
    # the day's totals instead of restarting from zero.
    carried: dict | None = None
    try:
        # NOTE: the snapshot is persisted by the broadcaster to ``settings.stats_dir``
        # (``_state/stats``), not ``state_dir`` — reading the wrong directory silently
        # returned None on every restart, which is how a session's stale/gap counters
        # came back as zeros. Its payload is the ``capture_status`` envelope, so the
        # counters live under ``global``, not at the top level.
        stats_dir = getattr(settings, "stats_dir", settings.state_dir)
        prior = load_capture_snapshot(stats_dir, trading_date)
        if prior:
            globals_ = prior.get("global") or {}
            streams = prior.get("per_underlying") or []
            carried = {
                "grid_gaps": globals_.get("grid_gaps"),
                "grid_seconds_lost": globals_.get("grid_seconds_lost"),
                "stale_seconds": globals_.get("stale_seconds"),
                "stale_events": globals_.get("stale_events"),
                "first_grid_ms": globals_.get("first_grid_ms"),
                # Recovery effort is process-local too: without carrying it, a session
                # that escalated twice reports the last process's zero (the reason the
                # 08-06 history showed 3 reconnects against 27 real attempts).
                "longest_stale_spell_seconds": globals_.get("longest_stale_spell_seconds"),
            }
            logger.info(
                "found today's persisted telemetry (%d streams) to carry over: "
                "%s gap(s), %ss lost, %ss stale",
                len(streams),
                carried["grid_gaps"],
                carried["grid_seconds_lost"],
                carried["stale_seconds"],
            )
    except Exception as exc:  # noqa: BLE001 - telemetry carry-over must never block start
        logger.warning("could not read prior capture snapshot: %s", exc)

    resume = engine.resume_from_disk(carried)

    # The subscription universe, computed from the instruments actually resolved above
    # rather than from a remembered estimate. The planner reports headroom against the
    # broker's per-connection limit so adding a data domain is a measured decision.
    token_groups: dict[str, list[int] | tuple[int, ...] | set[int]] = {
        name: list(table.tokens) for name, table in index_tables.items()
    }
    if stock_matrix is not None:
        token_groups["STOCKS"] = list(stock_matrix.tokens)
    if index_fno_matrix is not None:
        token_groups["INDICES_FnO"] = list(index_fno_matrix.tokens)
    subscription = plan_subscriptions(
        token_groups,
        limit=getattr(settings, "broker_subscription_limit", 3_000),
        safety_margin_pct=getattr(settings, "broker_subscription_safety_margin_pct", 10.0),
        max_connections=getattr(settings, "broker_max_connections", 3),
    )
    tokens = list(subscription.tokens)

    monitor = CaptureMonitor(
        index_tables,
        stock_matrix,
        index_writers,
        stock_writer,
        engine=engine,
        market_data_path=settings.market_data_path,
        clock=clock,
        expected_frames=expected_frames,
        capture_start_ms=engine.first_capture_ms or clock(),
        session_registry=session_registry,
        subscription=subscription,
        index_fno_matrix=index_fno_matrix,
        index_fno_writer=index_fno_writer,
    )
    if resume.get("resumed"):
        logger.info(
            "capture resumed: day total starts at %d frames (downtime %ds)",
            resume["frames_on_disk"],
            resume["downtime_seconds"],
        )
    broadcaster = None
    if hub is not None:
        from app.capture.broadcaster import Broadcaster

        broadcaster = Broadcaster(
            index_tables,
            stock_matrix,
            hub,
            monitor=monitor,
            index_fno_matrix=index_fno_matrix,
            clock=clock,
            stats_state_dir=getattr(settings, "stats_dir", None),
            trading_date=trading_date,
        )


    bridge = TickerBridge(
        settings.kite_api_key,
        access_token,
        tokens,
        ticker_factory=ticker_factory,
        token_provider=token_provider,
        clock=clock,
    )
    # The market-data bus is attached last and deliberately after every capture concern is
    # already constructed, so a bus that cannot connect changes nothing about the session.
    from app.bus.publisher import build_publisher
    from app.bus.session import build_instruments

    bus = build_publisher(settings)

    def _bus_session():
        return trading_date, build_instruments(index_tables, stock_matrix, index_fno_matrix)

    engine.set_bus(
        bus,
        session_provider=_bus_session,
        interval_s=float(
            getattr(settings, "market_data_bus_session_interval_seconds", 60.0)
        ),
    )
    monitor.bus = bus
    if getattr(bus, "enabled", True):
        date, instruments = _bus_session()
        bus.publish_session(trading_date=date, instruments=instruments)

    monitor.bridge = bridge
    # The socket-replacement rung, tried before escalating to a process restart.
    engine.set_reconnect_fn(bridge.reconnect)

    return CaptureContext(
        engine=engine,
        bridge=bridge,
        monitor=monitor,
        index_tables=index_tables,
        stock_matrix=stock_matrix,
        tokens=tokens,
        trading_date=trading_date,
        broadcaster=broadcaster,
        skipped_indices=skipped,
        subscription=subscription,
        bus=bus,
    )


async def run_capture(
    context: CaptureContext,
    stop_event,
    *,
    interval_s: float = 1.0,
    connect_timeout_s: float = CONNECT_TIMEOUT_S,
) -> None:  # pragma: no cover - live loop, integration-only
    """Run capture until stopped, surfacing a ticker authentication failure.

    The 1 Hz grid is entered only after the socket has *proven* it is live. Starting the
    grid on an unconfirmed socket is what turned a dead feed into a ~295-second hole plus
    five zero-filled frames every affected session: nothing read ``bridge.connected``, so
    the first signal was the freshness monitor 5 s later and the first *action* was the
    40 s escalation deadline.
    """
    context.bridge.bind_loop()
    try:
        context.bridge.start()
    except Exception as exc:
        context.bridge.stop()
        if is_authentication_error(exc):
            raise KiteAuthenticationError("Kite ticker rejected the active access token") from exc
        raise
    try:
        await _await_live_feed(context.bridge, connect_timeout_s)
    except BaseException:
        context.bridge.stop()
        raise
    engine_task = asyncio.create_task(
        context.engine.run(
            context.bridge,
            stop_event,
            interval_s=interval_s,
            broadcaster=context.broadcaster,
        )
    )
    auth_task = asyncio.create_task(context.bridge.auth_failed.wait())
    try:
        done, _pending = await asyncio.wait(
            {engine_task, auth_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if engine_task in done:
            await engine_task
            return

        engine_task.cancel()
        result = (await asyncio.gather(engine_task, return_exceptions=True))[0]
        if isinstance(result, BaseException) and not isinstance(
            result, asyncio.CancelledError
        ):
            raise result
        raise KiteAuthenticationError("Kite ticker rejected the active access token")
    finally:
        auth_task.cancel()
        await asyncio.gather(auth_task, return_exceptions=True)
        if not engine_task.done():
            engine_task.cancel()
            await asyncio.gather(engine_task, return_exceptions=True)
        context.bridge.stop()


async def _await_live_feed(bridge: TickerBridge, timeout_s: float) -> None:
    """Wait for a confirmed subscription, or fail with the reason.

    Races the confirmation against an authentication failure so a rejected token is
    reported as such rather than as a generic connect timeout.
    """
    auth = asyncio.create_task(bridge.auth_failed.wait())
    live = asyncio.create_task(bridge.connect_confirmed.wait())
    try:
        done, _pending = await asyncio.wait(
            {auth, live}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        if auth in done:
            raise KiteAuthenticationError("Kite ticker rejected the active access token")
        if live in done:
            logger.info(
                "feed live: subscription confirmed after %d connect attempt(s)",
                getattr(bridge, "connect_attempts", 1),
            )
            return
        raise TickerConnectFailed(
            f"ticker did not confirm a live subscription within {timeout_s:g}s "
            f"({len(getattr(bridge, 'tokens', ()))} tokens); refusing to start the "
            "1 Hz grid against a socket that never connected"
        )
    finally:
        for task in (auth, live):
            if not task.done():
                task.cancel()
        await asyncio.gather(auth, live, return_exceptions=True)
