"""Operator unlock flow backed by short-lived opaque browser sessions.

The long-lived operator token is accepted only by the unlock endpoint. It is hashed in
memory, compared in constant time, and exchanged for a random HttpOnly cookie. Browser
session values are also stored only as SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

OPERATOR_COOKIE_NAME = "operator_session"
_PUBLIC_PATHS = frozenset({"/health", "/api/operator/status", "/api/operator/unlock"})
_PROTECTED_PREFIXES = ("/api/auth", "/api/capture", "/monitor", "/docs", "/redoc")


@dataclass(frozen=True)
class BrowserSession:
    digest: bytes
    expires_at: float


@dataclass(frozen=True)
class FailedAttempt:
    occurred_at: float


class UnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=256)


class UnlockResponse(BaseModel):
    unlocked: bool
    expires_at: int


class OperatorStatusResponse(BaseModel):
    unlocked: bool


class OperatorAuthService:
    """Authenticate the operator and manage short-lived, in-memory sessions."""

    def __init__(
        self,
        *,
        operator_token: str,
        session_ttl_seconds: int,
        login_max_attempts: int,
        login_window_seconds: int,
        cookie_secure: bool,
        allowed_origins: Sequence[str],
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        self._operator_token_digest = self._digest(operator_token)
        self._session_ttl_seconds = session_ttl_seconds
        self._login_max_attempts = login_max_attempts
        self._login_window_seconds = login_window_seconds
        self.cookie_secure = cookie_secure
        self.allowed_origins = frozenset(allowed_origins)
        self._clock = clock
        self._token_factory = token_factory
        self._sessions: tuple[BrowserSession, ...] = ()
        self._failures: dict[str, tuple[FailedAttempt, ...]] = {}
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            "OperatorAuthService(session_ttl_seconds="
            f"{self._session_ttl_seconds}, cookie_secure={self.cookie_secure})"
        )

    @property
    def session_ttl_seconds(self) -> int:
        return self._session_ttl_seconds

    @staticmethod
    def _digest(value: str) -> bytes:
        return hashlib.sha256(value.encode("utf-8")).digest()

    def verify_operator_token(self, supplied_token: str) -> bool:
        supplied_digest = self._digest(supplied_token)
        return secrets.compare_digest(supplied_digest, self._operator_token_digest)

    def unlock(self, client_id: str, supplied_token: str) -> tuple[str, int]:
        """Validate one login attempt and return an opaque cookie plus its expiry."""
        now = self._clock()
        with self._lock:
            failures = self._current_failures(client_id, now)
            if len(failures) >= self._login_max_attempts:
                self._failures = {**self._failures, client_id: failures}
                raise HTTPException(status_code=429, detail="too many unlock attempts; retry later")

            if not self.verify_operator_token(supplied_token):
                self._failures = {
                    **self._failures,
                    client_id: (*failures, FailedAttempt(occurred_at=now)),
                }
                raise HTTPException(status_code=401, detail="invalid operator credential")

            self._failures = {
                key: value for key, value in self._failures.items() if key != client_id
            }
            cookie_value = self._token_factory()
            expires_at = now + self._session_ttl_seconds
            current_sessions = tuple(
                session for session in self._sessions if session.expires_at > now
            )
            self._sessions = (
                *current_sessions,
                BrowserSession(digest=self._digest(cookie_value), expires_at=expires_at),
            )
            return cookie_value, int(expires_at)

    def is_authenticated(self, cookie_value: str | None) -> bool:
        if not cookie_value:
            return False
        supplied_digest = self._digest(cookie_value)
        now = self._clock()
        with self._lock:
            current_sessions = tuple(
                session for session in self._sessions if session.expires_at > now
            )
            self._sessions = current_sessions
            return any(
                secrets.compare_digest(supplied_digest, session.digest)
                for session in current_sessions
            )

    def revoke(self, cookie_value: str | None) -> None:
        if not cookie_value:
            return
        supplied_digest = self._digest(cookie_value)
        with self._lock:
            self._sessions = tuple(
                session
                for session in self._sessions
                if not secrets.compare_digest(supplied_digest, session.digest)
            )

    def _current_failures(self, client_id: str, now: float) -> tuple[FailedAttempt, ...]:
        cutoff = now - self._login_window_seconds
        return tuple(
            attempt
            for attempt in self._failures.get(client_id, ())
            if attempt.occurred_at > cutoff
        )


def _service(request: Request) -> OperatorAuthService | None:
    return getattr(request.app.state, "operator_auth", None)


def _require_allowed_origin(request: Request, service: OperatorAuthService) -> None:
    if request.headers.get("origin") not in service.allowed_origins:
        raise HTTPException(status_code=403, detail="request origin is not allowed")


def _client_id(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def create_operator_router() -> APIRouter:
    router = APIRouter(prefix="/api/operator", tags=["operator"])

    @router.get("/status", response_model=OperatorStatusResponse)
    async def operator_status(request: Request) -> OperatorStatusResponse:
        service = _service(request)
        cookie = request.cookies.get(OPERATOR_COOKIE_NAME)
        return OperatorStatusResponse(
            unlocked=service is not None and service.is_authenticated(cookie)
        )

    @router.post("/unlock", response_model=UnlockResponse)
    async def unlock(request: Request, body: UnlockRequest, response: Response) -> UnlockResponse:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="operator authentication is not configured")
        _require_allowed_origin(request, service)
        cookie_value, expires_at = service.unlock(_client_id(request), body.token)
        response.set_cookie(
            key=OPERATOR_COOKIE_NAME,
            value=cookie_value,
            max_age=service.session_ttl_seconds,
            httponly=True,
            secure=service.cookie_secure,
            samesite="strict",
            path="/",
        )
        return UnlockResponse(unlocked=True, expires_at=expires_at)

    @router.post("/lock", status_code=204)
    async def lock(request: Request, response: Response) -> Response:
        service = _service(request)
        if service is None:
            raise HTTPException(status_code=503, detail="operator authentication is not configured")
        _require_allowed_origin(request, service)
        service.revoke(request.cookies.get(OPERATOR_COOKIE_NAME))
        response.delete_cookie(
            key=OPERATOR_COOKIE_NAME,
            httponly=True,
            secure=service.cookie_secure,
            samesite="strict",
            path="/",
        )
        response.status_code = 204
        return response

    return router


def _requires_operator_auth(path: str) -> bool:
    if path in _PUBLIC_PATHS or path == "/openapi.json":
        return path == "/openapi.json"
    if path == "/api/capture/maintenance" or path.startswith("/api/capture/maintenance/"):
        return False
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PROTECTED_PREFIXES)


class OperatorAuthMiddleware(BaseHTTPMiddleware):
    """Require a valid operator cookie on sensitive HTTP routes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        if request.method == "OPTIONS" or not _requires_operator_auth(request.url.path):
            return await call_next(request)
        service = _service(request)
        if service is None:
            return JSONResponse(
                status_code=503,
                content={"detail": "operator authentication is not configured"},
            )
        if not service.is_authenticated(request.cookies.get(OPERATOR_COOKIE_NAME)):
            return JSONResponse(status_code=401, content={"detail": "operator unlock required"})
        return await call_next(request)
