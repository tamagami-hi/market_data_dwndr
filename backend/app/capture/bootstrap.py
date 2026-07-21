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

import logging
from dataclasses import dataclass, field

from app.capture.engine import CaptureEngine, build_index_writer, build_stock_writer
from app.capture.monitor import CaptureMonitor
from app.chain.assembler import build_option_chain
from app.chain.config import VIX_SYMBOL, get_index_config
from app.chain.table import IndexTable
from app.kite.instruments import InstrumentStore
from app.ops.calendar import TradingCalendar
from app.session import now_ms
from app.stocks.board import discover_fno_board
from app.stocks.matrix import StockMatrix

logger = logging.getLogger(__name__)


@dataclass
class CaptureContext:
    """Everything needed to run (and observe) a capture session."""

    engine: CaptureEngine
    bridge: object  # TickerBridge
    monitor: CaptureMonitor
    index_tables: dict[str, IndexTable]
    stock_matrix: StockMatrix | None
    tokens: list[int]
    trading_date: str
    broadcaster: object | None = None
    skipped_indices: list[str] = field(default_factory=list)


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
    clock=now_ms,
) -> CaptureContext:
    """Assemble a :class:`CaptureContext` for today's session."""
    calendar = TradingCalendar(
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
        logger.warning("stock board discovery failed: %s", exc)

    if not index_tables and stock_matrix is None:
        raise RuntimeError("bootstrap produced no index chains and no stock board")

    engine = CaptureEngine(index_tables, stock_matrix, index_writers, stock_writer, clock=clock)
    monitor = CaptureMonitor(
        index_tables,
        stock_matrix,
        index_writers,
        stock_writer,
        engine=engine,
        market_data_path=settings.market_data_path,
        clock=clock,
    )
    broadcaster = None
    if hub is not None:
        from app.capture.broadcaster import Broadcaster

        broadcaster = Broadcaster(index_tables, stock_matrix, hub, monitor=monitor, clock=clock)

    tokens = sorted(
        {t for table in index_tables.values() for t in table.tokens}
        | (set(stock_matrix.tokens) if stock_matrix else set())
    )

    from app.kite.ticker import TickerBridge

    bridge = TickerBridge(
        settings.kite_api_key, access_token, tokens, ticker_factory=ticker_factory
    )
    monitor.bridge = bridge

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
    )


async def run_capture(
    context: CaptureContext,
    stop_event,
    *,
    interval_s: float = 1.0,
) -> None:  # pragma: no cover - live loop, integration-only
    """Start the ticker and run the 1 Hz engine until ``stop_event`` is set."""
    context.bridge.bind_loop()
    context.bridge.start()
    try:
        await context.engine.run(
            context.bridge, stop_event, interval_s=interval_s, broadcaster=context.broadcaster
        )
    finally:
        context.bridge.stop()
