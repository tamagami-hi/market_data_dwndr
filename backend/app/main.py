"""FastAPI application entrypoint.

Serves ``/health``, the self-contained ``/monitor`` dashboard, the ``/api/auth`` routes
(session status + automated login), and the ``/ws/{topic}`` WebSocket topics. At startup
it builds the session service and reports whether today's Kite session already exists
(resume) so a mid-day restart doesn't force a re-login.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app import __version__
from app.api.auth import create_auth_router
from app.api.capture import create_capture_router
from app.logging_config import configure_logging
from app.ws.routes import ConnectionManager, create_ws_router

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def cors_origins() -> list[str]:
    """Allowed browser origins from env (``FRONTEND_URL``); empty if unconfigured."""
    try:
        from app.config import get_settings

        return get_settings().cors_origins
    except Exception:  # noqa: BLE001 - env-less (e.g. tests) -> no cross-origin allowed
        return []


def _init_session_service(app: FastAPI) -> None:
    """Build the session service from env; report resume state. Never raises."""
    try:
        from app.config import get_settings
        from app.session_service import SessionService

        settings = get_settings()
        configure_logging(settings.log_level)
        service = SessionService(settings)
        app.state.settings = settings
        app.state.session_service = service

        from app.api.capture import CaptureController

        app.state.capture_controller = CaptureController(settings, service, app.state.ws_hub)

        status = service.status()
        if status["authenticated"]:
            logger.info(
                "resumed Kite session for %s (market phase: %s)",
                status["trading_date"],
                status["market_phase"],
            )
        else:
            logger.info(
                "no Kite session for %s yet — run `md-login` or start the staged login API",
                status["trading_date"],
            )
    except Exception as exc:  # noqa: BLE001 - unconfigured env shouldn't crash the app
        app.state.settings = None
        app.state.session_service = None
        app.state.capture_controller = None
        logger.warning("session service not initialised (backend unconfigured): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: resume today's session; wire capture in later."""
    _init_session_service(app)
    try:
        yield
    finally:
        service = getattr(app.state, "session_service", None)
        if service is not None:
            service.close()


app = FastAPI(
    title="market_data_dwndr",
    version=__version__,
    summary="Zerodha Kite market-data downloader (capture only, no trading).",
    lifespan=lifespan,
)

# WebSocket broadcast hub (topics: market-data, stocks, capture-status, session,
# historical-jobs). The capture engine / monitor / broadcaster push frames here.
ws_hub = ConnectionManager()
app.state.ws_hub = ws_hub
app.state.session_service = None
app.state.capture_controller = None

# CORS: the frontend runs on a different origin/port, so the browser needs the backend
# to allow its origin. Origins come from FRONTEND_URL in the environment (no hardcoded
# ports). WebSocket connections are not subject to CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(create_ws_router(ws_hub))
app.include_router(create_auth_router())
app.include_router(create_capture_router())


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "version": __version__}


@app.get("/monitor", response_class=HTMLResponse, tags=["ui"])
async def monitor_page() -> str:
    """Serve the self-contained Capture Monitor dashboard (live WS telemetry)."""
    return (STATIC_DIR / "monitor.html").read_text(encoding="utf-8")
