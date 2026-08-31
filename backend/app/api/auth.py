"""Auth API: session status plus retained manual fallback routes.

Routes (mounted under ``/api/auth``):

    GET  /api/auth/status      -> session, automation, and capture snapshot (no secrets)
    POST /api/auth/login       -> exchange a manual browser ``request_token``
    GET  /api/auth/login-url   -> Zerodha OAuth URL for a manual API fallback

Normal VPS operation acquires the token through ``DailyAutomationService``; these login
routes remain available for operational fallback but are not initiated by the frontend.
The session service is read from ``request.app.state.session_service`` (built at startup).
When the backend is unconfigured, routes degrade gracefully instead of returning 500.

Every route that starts or mutates a login attempt requires the operator credential
(``RELEASE_MAINTENANCE_TOKEN``, sent as ``X-Release-Maintenance-Token``) — the same secret
and header the release scripts already use, rather than a second independent mechanism.
The ``Origin`` check retained alongside it is CSRF mitigation only: it is honoured by
browsers and trivially forged by any other client, so it was never authentication.
``GET /api/auth/status`` stays unauthenticated because the frontend depends on it.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi import status as http_status
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from starlette.concurrency import run_in_threadpool

from app.config import secret_text
from app.kite.external_token import ExternalTokenError
from app.kite.login import KiteLoginError
from app.kite.login_flow import LoginProgress

logger = logging.getLogger(__name__)

OPERATOR_TOKEN_HEADER = "X-Release-Maintenance-Token"


class OperatorAuthenticationError(Exception):
    """Raised when the operator credential is missing or does not match."""


class OperatorAuthUnavailableError(Exception):
    """Raised when no operator credential is configured, so the route must stay closed."""



class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_token: Annotated[str, Field(min_length=1)]
    risk_free_rate: Annotated[FiniteFloat, Field(ge=0)]


class TotpRequest(BaseModel):
    totp: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class RiskFreeRateRequest(BaseModel):
    risk_free_rate: Annotated[FiniteFloat, Field(ge=0)]


class LoginProgressResponse(BaseModel):
    attempt_id: str
    step: Literal["awaiting_totp", "awaiting_risk_free_rate"]
    method: Literal["shared_session", "local_credentials"]
    trading_date: str
    expires_at: int


class LoginResponse(BaseModel):
    authenticated: bool
    trading_date: str
    risk_free_rate: float | None = None
    risk_free_rate_as_of: str | None = None
    capture_ready: bool = False


def _service(request: Request):
    return getattr(request.app.state, "session_service", None)


def _require_operator(service, provided_token: str | None) -> None:
    """Authorize an operator-only login route against ``RELEASE_MAINTENANCE_TOKEN``.

    Fails closed: with no credential configured the route is refused rather than left
    open, because these endpoints start upstream Zerodha authentication and overwrite the
    day's session.
    """
    expected = secret_text(getattr(service.settings, "release_maintenance_token", None))
    if expected is None:
        raise OperatorAuthUnavailableError(
            "operator authorization is not configured; set RELEASE_MAINTENANCE_TOKEN to "
            "use the manual login recovery routes"
        )
    supplied = (provided_token or "").encode("utf-8")
    if not secrets.compare_digest(supplied, expected.encode("utf-8")):
        raise OperatorAuthenticationError("invalid operator credential")


def _authorize(service, provided_token: str | None) -> None:
    """Authorize an operator-only route.

    Token only, exactly like ``/api/capture/maintenance``. The ``Origin`` check is not
    layered on top: a request that must carry a secret header cannot be forged by a
    cross-site browser form (a custom header forces a CORS preflight), and requiring an
    allowed ``Origin`` would break the operator's own ``curl``-based recovery.
    """
    try:
        _require_operator(service, provided_token)
    except OperatorAuthUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OperatorAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _progress_response(progress: LoginProgress) -> LoginProgressResponse:
    return LoginProgressResponse(
        attempt_id=progress.attempt_id,
        step=progress.step.value,
        method=progress.method.value,
        trading_date=progress.trading_date,
        expires_at=progress.expires_at,
    )


def _login_response(service, state) -> LoginResponse:
    return LoginResponse(
        authenticated=True,
        trading_date=state.trading_date,
        risk_free_rate=state.risk_free_rate,
        risk_free_rate_as_of=state.risk_free_rate_as_of,
        capture_ready=state.capture_ready,
    )


async def _run_flow_action(action, *args):
    try:
        return await run_in_threadpool(action, *args)
    except (KiteLoginError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExternalTokenError as exc:
        logger.warning("external token lookup failed")
        raise HTTPException(
            status_code=502,
            detail="shared token service is unavailable; retry later",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("staged login failed")
        raise HTTPException(status_code=502, detail="login failed; retry the login flow") from exc


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.get("/status")
    async def status(request: Request) -> dict:
        service = _service(request)
        if service is None:
            return {"configured": False, "authenticated": False}
        result = service.status()
        automation = getattr(request.app.state, "daily_automation", None)
        if automation is not None:
            result = {**result, "automation": automation.status()}
        controller = getattr(request.app.state, "capture_controller", None)
        if controller is not None:
            result = {**result, "capture": controller.status()}
        return result

    @router.get("/login-url")
    async def login_url(
        request: Request,
        operator_token: Annotated[
            str | None,
            Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256),
        ] = None,
    ) -> dict:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        _authorize(service, operator_token)
        return {"login_url": service.login_url()}

    @router.post(
        "/login/start",
        response_model=LoginProgressResponse | LoginResponse,
        status_code=http_status.HTTP_202_ACCEPTED,
    )
    async def start_login(
        request: Request,
        operator_token: Annotated[
            str | None,
            Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256),
        ] = None,
    ) -> LoginProgressResponse | LoginResponse:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        _authorize(service, operator_token)
        active_session = service.active_session()
        if active_session is not None:
            return _login_response(service, active_session)
        progress = await _run_flow_action(service.start_login)
        return _progress_response(progress)

    @router.post(
        "/login/{attempt_id}/totp",
        response_model=LoginProgressResponse,
    )
    async def submit_totp(
        request: Request,
        attempt_id: str,
        body: TotpRequest,
        operator_token: Annotated[
            str | None,
            Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256),
        ] = None,
    ) -> LoginProgressResponse:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        _authorize(service, operator_token)
        progress = await _run_flow_action(service.submit_login_totp, attempt_id, body.totp)
        return _progress_response(progress)

    @router.post(
        "/login/{attempt_id}/complete",
        response_model=LoginResponse,
    )
    async def complete_login(
        request: Request,
        attempt_id: str,
        body: RiskFreeRateRequest,
        operator_token: Annotated[
            str | None,
            Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256),
        ] = None,
    ) -> LoginResponse:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        _authorize(service, operator_token)
        state = await _run_flow_action(service.complete_login, attempt_id, body.risk_free_rate)
        return _login_response(service, state)

    @router.delete("/login/{attempt_id}", status_code=http_status.HTTP_204_NO_CONTENT)
    async def cancel_login(
        request: Request,
        attempt_id: str,
        operator_token: Annotated[
            str | None,
            Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256),
        ] = None,
    ) -> Response:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        _authorize(service, operator_token)
        await _run_flow_action(service.cancel_login, attempt_id)
        return Response(status_code=http_status.HTTP_204_NO_CONTENT)

    @router.post("/login", response_model=LoginResponse)
    async def login(
        request: Request,
        body: LoginRequest,
        operator_token: Annotated[
            str | None,
            Header(alias=OPERATOR_TOKEN_HEADER, min_length=1, max_length=256),
        ] = None,
    ) -> LoginResponse:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="backend not configured")
        _authorize(service, operator_token)
        try:
            state = service.login(
                request_token=body.request_token,
                risk_free_rate=body.risk_free_rate,
            )
        except (KiteLoginError, ValueError) as exc:
            logger.warning("manual browser login failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=400,
                detail="manual login could not be completed",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("login failed")
            raise HTTPException(status_code=502, detail="manual login failed") from exc

        return _login_response(service, state)

    return router
