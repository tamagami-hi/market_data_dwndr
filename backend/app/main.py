"""FastAPI application entrypoint.

Phase 0 scaffold: exposes ``/health`` so the service is runnable
(``uvicorn app.main:app``). Later phases wire in Kite auth, capture, WebSocket
routes, and the historical downloader.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.ws.routes import ConnectionManager, create_ws_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan. Capture/scheduler wiring lands in later phases."""
    # Startup hooks (session-state load, scheduler) will go here (Phase 3/5).
    yield
    # Shutdown hooks (flush writers, EOD sweep) will go here (Phase 5).


app = FastAPI(
    title="market_data_dwndr",
    version=__version__,
    summary="Zerodha Kite market-data downloader (capture only, no trading).",
    lifespan=lifespan,
)

# WebSocket broadcast hub (topics: market-data, stocks, capture-status, session,
# historical-jobs). The capture engine / monitor push frames here in later phases.
ws_hub = ConnectionManager()
app.state.ws_hub = ws_hub
app.include_router(create_ws_router(ws_hub))


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "version": __version__}
