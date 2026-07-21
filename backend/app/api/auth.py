"""Auth API: session status + frontend-triggered automated login.

Routes (mounted under ``/api/auth``):

    GET  /api/auth/status      -> current session / market-phase snapshot (no secrets)
    POST /api/auth/login       -> run the automated login (TOTP from the request body),
                                  or exchange a browser ``request_token``
    GET  /api/auth/login-url   -> Zerodha OAuth URL for the manual browser fallback

The service is read from ``request.app.state.session_service`` (built at startup). When
the backend is unconfigured (missing env), routes degrade gracefully instead of 500.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.kite.login import KiteLoginError

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    totp: str | None = None
    request_token: str | None = None
    risk_free_rate: float | None = None


class LoginResponse(BaseModel):
    authenticated: bool
    trading_date: str
    access_token: str | None = None  # masked
    risk_free_rate: float | None = None


def _service(request: Request):
    return getattr(request.app.state, "session_service", None)


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/status")
    async def status(request: Request) -> dict:
        service = _service(request)
        if service is None:
            return {"configured": False, "authenticated": False}
        return service.status()

    @router.get("/login-url")
    async def login_url(request: Request) -> dict:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        return {"login_url": service.login_url()}

    @router.post("/login", response_model=LoginResponse)
    async def login(request: Request, body: LoginRequest) -> LoginResponse:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        if not body.request_token and not service.credentials_present:
            raise HTTPException(
                status_code=400,
                detail="KITE_USER_ID/KITE_PASSWORD not configured; supply a request_token instead",
            )
        try:
            state = service.login(
                totp=body.totp,
                request_token=body.request_token,
                risk_free_rate=body.risk_free_rate,
            )
        except (KiteLoginError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface upstream/login failures as 502
            logger.exception("login failed")
            raise HTTPException(status_code=502, detail=f"login failed: {exc}") from exc

        masked = service.status()["access_token"]
        return LoginResponse(
            authenticated=True,
            trading_date=state.trading_date,
            access_token=masked,
            risk_free_rate=state.risk_free_rate,
        )

    return router
