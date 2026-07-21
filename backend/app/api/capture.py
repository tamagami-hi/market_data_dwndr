"""Capture control API — start/stop the live 1 Hz engine from the frontend.

Runs the capture **inside the FastAPI process** so it shares the WebSocket hub and the
broadcaster can push MarketHeader/OptionGrid/StockBoard/CaptureStatus to connected
clients. (For headless capture with no UI, use the ``md-capture`` CLI instead.)

    GET  /api/capture/status   -> running? which indices/stocks/tokens
    POST /api/capture/start    -> bootstrap + run (requires a logged-in session)
    POST /api/capture/stop     -> stop the running session
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)


class CaptureError(Exception):
    """Raised for invalid capture control requests (already running, not logged in…)."""


class CaptureController:
    """Owns the single in-process capture task and its lifecycle."""

    def __init__(
        self,
        settings,
        session_service,
        hub,
        *,
        bootstrap_fn=None,
        run_fn=None,
    ) -> None:
        self.settings = settings
        self.session_service = session_service
        self.hub = hub
        self._bootstrap_fn = bootstrap_fn
        self._run_fn = run_fn
        self._task: asyncio.Task | None = None
        self._context = None
        self._stop: asyncio.Event | None = None
        self._error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _resolve_fns(self):
        bootstrap_fn = self._bootstrap_fn
        run_fn = self._run_fn
        if bootstrap_fn is None or run_fn is None:
            from app.capture.bootstrap import bootstrap_capture, run_capture

            bootstrap_fn = bootstrap_fn or bootstrap_capture
            run_fn = run_fn or run_capture
        return bootstrap_fn, run_fn

    async def start(self) -> dict:
        if self.running:
            raise CaptureError("capture is already running")
        session = self.session_service.active_session()
        if session is None or not session.access_token:
            raise CaptureError("not logged in — run `md-login` or POST /api/auth/login first")

        bootstrap_fn, run_fn = self._resolve_fns()
        context = bootstrap_fn(
            self.settings, session.access_token, session.risk_free_rate, hub=self.hub
        )
        self._context = context
        self._stop = asyncio.Event()
        self._error = None

        async def _runner() -> None:
            try:
                await run_fn(context, self._stop)
            except Exception as exc:  # noqa: BLE001 - record, don't crash the server
                self._error = str(exc)
                logger.exception("capture task crashed")

        self._task = asyncio.create_task(_runner())
        logger.info("capture started for %s (%d tokens)", context.trading_date, len(context.tokens))
        return self.status()

    async def stop(self) -> dict:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        self._stop = None
        return self.status()

    def status(self) -> dict:
        ctx = self._context
        return {
            "available": True,
            "running": self.running,
            "trading_date": ctx.trading_date if ctx else None,
            "indices": list(ctx.index_tables) if ctx else [],
            "stocks": len(ctx.stock_matrix.stock_refs) if ctx and ctx.stock_matrix else 0,
            "tokens": len(ctx.tokens) if ctx else 0,
            "skipped_indices": ctx.skipped_indices if ctx else [],
            "error": self._error,
        }


def _controller(request: Request) -> CaptureController | None:
    return getattr(request.app.state, "capture_controller", None)


def create_capture_router() -> APIRouter:
    router = APIRouter(prefix="/api/capture", tags=["capture"])

    @router.get("/status")
    async def status(request: Request) -> dict:
        controller = _controller(request)
        if controller is None:
            return {"available": False, "running": False}
        return controller.status()

    @router.post("/start")
    async def start(request: Request) -> dict:
        controller = _controller(request)
        if controller is None:
            raise HTTPException(status_code=503, detail="capture not available (unconfigured)")
        try:
            return await controller.start()
        except CaptureError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/stop")
    async def stop(request: Request) -> dict:
        controller = _controller(request)
        if controller is None:
            raise HTTPException(status_code=503, detail="capture not available")
        return await controller.stop()

    return router
