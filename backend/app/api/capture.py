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
import secrets
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Request
from pydantic import BaseModel

from app.capture.maintenance import (
    MaintenanceConflictError,
    MaintenanceLease,
    MaintenanceLeaseNotFoundError,
    MaintenanceLeaseStore,
)

logger = logging.getLogger(__name__)


class CaptureError(Exception):
    """Raised for invalid capture control requests (already running, not logged in…)."""


class MaintenanceAuthenticationError(Exception):
    """Raised when the internal maintenance credential is missing or invalid."""


class MaintenanceUnavailableError(Exception):
    """Raised when release maintenance is not configured."""


class MaintenanceLeaseResponse(BaseModel):
    lease_id: str
    expires_at: str


class MaintenanceReleaseResponse(BaseModel):
    released: bool


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
        maintenance_store: MaintenanceLeaseStore | None = None,
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
        self._has_failed = False
        self._lifecycle_lock = asyncio.Lock()
        self._maintenance_token = _secret_value(
            getattr(settings, "release_maintenance_token", None)
        )
        state_dir = getattr(settings, "state_dir", None)
        if maintenance_store is None and state_dir is not None:
            maintenance_store = MaintenanceLeaseStore(
                state_dir,
                ttl_seconds=getattr(settings, "release_maintenance_ttl_seconds", 900),
            )
        self._maintenance_store = maintenance_store

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
        async with self._lifecycle_lock:
            return await self._start_unlocked()

    async def _start_unlocked(self) -> dict:
        if self._maintenance_store is not None and self._maintenance_store.active() is not None:
            raise CaptureError("capture is paused for release maintenance")
        if self._has_failed:
            raise CaptureError("previous capture failed; restart the backend before resuming")
        if self.running:
            raise CaptureError("capture is already running")
        session = self.session_service.active_session()
        if session is None or not session.access_token:
            raise CaptureError("not logged in — run `md-login` or POST /api/auth/login first")
        from app.session import is_session_capture_ready

        if not is_session_capture_ready(session):
            raise CaptureError("risk-free rate update is required before capture")

        bootstrap_fn, run_fn = self._resolve_fns()
        context = bootstrap_fn(
            self.settings, session.access_token, session.risk_free_rate, hub=self.hub
        )
        self._context = context
        self._stop = asyncio.Event()
        self._error = None
        self._has_failed = False

        async def _runner() -> None:
            try:
                await run_fn(context, self._stop)
            except Exception as exc:  # noqa: BLE001 - record, don't crash the server
                self._has_failed = True
                self._error = "capture task failed; inspect backend logs"
                logger.error("capture task failed (%s)", type(exc).__name__)

        self._task = asyncio.create_task(_runner())
        logger.info("capture started for %s (%d tokens)", context.trading_date, len(context.tokens))
        return self.status()

    async def stop(self) -> dict:
        async with self._lifecycle_lock:
            return await self._stop_unlocked()

    async def _stop_unlocked(self) -> dict:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10.0)
            except TimeoutError:
                self._has_failed = True
                self._error = "capture task failed; inspect backend logs"
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            except asyncio.CancelledError:
                if not self._task.cancelled():
                    raise
        self._task = None
        self._stop = None
        if self._has_failed:
            raise CaptureError("capture did not flush and stop safely")
        return self.status()

    async def acquire_maintenance(self, provided_token: str | None) -> MaintenanceLease:
        self._authenticate_maintenance(provided_token)
        if self._maintenance_store is None:  # defensive; authentication checks this first
            raise MaintenanceUnavailableError("release maintenance is not configured")
        async with self._lifecycle_lock:
            lease = self._maintenance_store.acquire()
            await self._stop_unlocked()
            return lease

    async def release_maintenance(self, provided_token: str | None, lease_id: str) -> bool:
        self._authenticate_maintenance(provided_token)
        if self._maintenance_store is None:  # defensive; authentication checks this first
            raise MaintenanceUnavailableError("release maintenance is not configured")
        async with self._lifecycle_lock:
            self._maintenance_store.release(lease_id)
            return True

    def _authenticate_maintenance(self, provided_token: str | None) -> None:
        if self._maintenance_store is None or self._maintenance_token is None:
            raise MaintenanceUnavailableError("release maintenance is not configured")
        supplied = (provided_token or "").encode("utf-8")
        expected = self._maintenance_token.encode("utf-8")
        if not secrets.compare_digest(supplied, expected):
            raise MaintenanceAuthenticationError("invalid release maintenance credential")

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

    def stock_depth(self, symbol: str) -> dict:
        matrix = self._context.stock_matrix if self._context is not None else None
        if matrix is None:
            raise CaptureError("stock depth is unavailable until capture is initialised")
        from app.stocks.depth import stock_depth_snapshot

        snapshot = stock_depth_snapshot(matrix, symbol)
        if snapshot is None:
            raise CaptureError("stock symbol was not found in the active board")
        return snapshot


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

    @router.post("/maintenance", response_model=MaintenanceLeaseResponse)
    async def acquire_maintenance(
        request: Request,
        maintenance_token: Annotated[
            str | None,
            Header(alias="X-Release-Maintenance-Token", min_length=1, max_length=256),
        ] = None,
    ) -> MaintenanceLeaseResponse:
        controller = _controller(request)
        if controller is None:
            raise HTTPException(status_code=503, detail="capture not available")
        try:
            lease = await controller.acquire_maintenance(maintenance_token)
        except MaintenanceUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except MaintenanceAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except MaintenanceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return MaintenanceLeaseResponse(lease_id=lease.lease_id, expires_at=lease.expires_at)

    @router.delete("/maintenance/{lease_id}", response_model=MaintenanceReleaseResponse)
    async def release_maintenance(
        request: Request,
        lease_id: Annotated[str, Path(min_length=1, max_length=128)],
        maintenance_token: Annotated[
            str | None,
            Header(alias="X-Release-Maintenance-Token", min_length=1, max_length=256),
        ] = None,
    ) -> MaintenanceReleaseResponse:
        controller = _controller(request)
        if controller is None:
            raise HTTPException(status_code=503, detail="capture not available")
        try:
            released = await controller.release_maintenance(maintenance_token, lease_id)
        except MaintenanceUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except MaintenanceAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except MaintenanceLeaseNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return MaintenanceReleaseResponse(released=released)

    @router.get("/stocks/{symbol}/depth")
    async def stock_depth(request: Request, symbol: str) -> dict:
        controller = _controller(request)
        if controller is None:
            raise HTTPException(status_code=503, detail="capture not available")
        try:
            return controller.stock_depth(symbol)
        except CaptureError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router


def _secret_value(value: object) -> str | None:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    raw_value = getter() if callable(getter) else value
    text = str(raw_value)
    return text if text else None
