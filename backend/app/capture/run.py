"""``md-capture`` — run a headless capture session.

Resumes today's Kite session (from ``md-login``), bootstraps the index chains + F&O
stock board, subscribes the ticker, and runs the 1 Hz engine to ``.bin`` files. Stops on
Ctrl-C (leaves raw files so a restart can resume/append) or at market close (then
EOD-compresses). The frontend live view is served by the FastAPI app instead
(``/api/capture/*``); this command is for headless / cron-style capture.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

logger = logging.getLogger(__name__)


def resolve_session(service) -> tuple[str, float]:
    """Return ``(access_token, risk_free_rate)`` from today's session, or raise."""
    session = service.active_session()
    if session is None:
        raise RuntimeError(
            f"no Kite session for {service.trading_date()} — run `md-login` first"
        )
    if not session.access_token:
        raise RuntimeError("session has no access_token — re-run `md-login`")
    from app.session import is_session_capture_ready

    if not is_session_capture_ready(session):
        raise RuntimeError("risk-free rate is unavailable; capture cannot start")
    return session.access_token, session.risk_free_rate


async def _run(context, settings, *, interval_s: float, gate_market_hours: bool,
               force_eod: bool) -> bool:  # pragma: no cover - live loop
    """Drive capture until stop; returns True if an EOD sweep should run."""
    from app.capture.bootstrap import run_capture
    from app.ops.calendar import PHASE_OPEN, TradingCalendar
    from app.session import now_ms

    stop_event = asyncio.Event()
    state = {"eod": force_eod}
    loop = asyncio.get_running_loop()

    def _stop(reason: str) -> None:
        logger.info("stopping capture (%s)", reason)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop, sig.name)
        except (NotImplementedError, RuntimeError):
            pass  # e.g. Windows / non-main thread

    calendar = TradingCalendar(
        holidays=set(getattr(settings, "market_holidays", [])),
        timezone_name=settings.timezone,
        market_open=settings.market_open,
        market_close=settings.market_close,
    )

    async def _market_watch() -> None:
        while not stop_event.is_set():
            if calendar.phase(now_ms()) != PHASE_OPEN:
                state["eod"] = True
                _stop("market closed")
                return
            await asyncio.sleep(5)

    watcher = asyncio.create_task(_market_watch()) if gate_market_hours else None
    try:
        await run_capture(context, stop_event, interval_s=interval_s)
    finally:
        if watcher is not None:
            watcher.cancel()
    return state["eod"]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - orchestration
    parser = argparse.ArgumentParser(prog="md-capture", description="Run live capture")
    parser.add_argument("--interval", type=float, default=1.0, help="snapshot seconds (default 1)")
    parser.add_argument(
        "--ignore-market-hours",
        action="store_true",
        help="run regardless of market phase (don't auto-stop at close)",
    )
    parser.add_argument(
        "--eod", action="store_true", help="compress raw .bin on exit even if interrupted"
    )
    args = parser.parse_args(argv)

    from app.config import get_settings
    from app.logging_config import configure_logging

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)

    from app.capture.bootstrap import bootstrap_capture
    from app.session_service import SessionService

    service = SessionService(settings)
    try:
        access_token, risk_free_rate = resolve_session(service)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        context = bootstrap_capture(settings, access_token, risk_free_rate)
    except Exception as exc:  # noqa: BLE001
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1

    logger.info(
        "capturing %d indices + %s stocks (%d tokens) for %s",
        len(context.index_tables),
        len(context.stock_matrix.stock_refs) if context.stock_matrix else 0,
        len(context.tokens),
        context.trading_date,
    )

    should_eod = asyncio.run(
        _run(
            context,
            settings,
            interval_s=args.interval,
            gate_market_hours=not args.ignore_market_hours,
            force_eod=args.eod,
        )
    )

    if should_eod:
        from app.ops.eod import compress_raw_files

        result = compress_raw_files(
            settings.market_data_path,
            settings.archive_data_path,
            level=settings.zstd_level,
            threads=getattr(settings, "zstd_threads", 0),
        )
        logger.info("EOD: compressed %d files", len(result.compressed))

    print(f"capture finished for {context.trading_date}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
