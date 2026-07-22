"""Short-lived state machine for browser-driven automated Kite login."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Lock
from uuid import uuid4

from app.kite.external_token import ExternalTokenError, fetch_external_access_token
from app.kite.login import (
    HttpClient,
    KiteLoginError,
    PasswordChallenge,
    begin_login,
    build_kite_http_client,
    complete_totp,
    exchange_request_token,
    validate_access_token,
    validate_risk_free_rate,
)
from app.session import SessionState, now_ms, save_session

LOGIN_ATTEMPT_TTL_MS = 3 * 60 * 1_000
LOGIN_START_COOLDOWN_MS = 5_000
logger = logging.getLogger(__name__)


class LoginStep(StrEnum):
    AWAITING_TOTP = "awaiting_totp"
    AWAITING_RISK_FREE_RATE = "awaiting_risk_free_rate"


class LoginMethod(StrEnum):
    SHARED_SESSION = "shared_session"
    LOCAL_CREDENTIALS = "local_credentials"


@dataclass(frozen=True)
class LoginProgress:
    attempt_id: str
    step: LoginStep
    method: LoginMethod
    trading_date: str
    expires_at: int


@dataclass(frozen=True)
class _LoginAttempt:
    attempt_id: str
    step: LoginStep
    method: LoginMethod
    trading_date: str
    expires_at: int
    client: HttpClient | None = None
    challenge: PasswordChallenge | None = None
    request_token: str | None = None
    access_token: str | None = None

    def progress(self) -> LoginProgress:
        return LoginProgress(
            attempt_id=self.attempt_id,
            step=self.step,
            method=self.method,
            trading_date=self.trading_date,
            expires_at=self.expires_at,
        )


ClientFactory = Callable[[str | None, str | None], HttpClient]
ExternalTokenFetcher = Callable[[], str | None]
ExternalTokenValidator = Callable[[str], None]


class LoginCoordinator:
    """Own cookie-bearing login attempts without exposing Kite internals to clients."""

    def __init__(
        self,
        settings,
        *,
        client_factory: ClientFactory = build_kite_http_client,
        external_token_fetcher: ExternalTokenFetcher | None = None,
        external_token_validator: ExternalTokenValidator | None = None,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._external_token_fetcher: ExternalTokenFetcher
        if external_token_fetcher is None:

            def configured_token_fetcher() -> str | None:
                return fetch_external_access_token(settings)

            self._external_token_fetcher = configured_token_fetcher
        else:
            self._external_token_fetcher = external_token_fetcher
        self._external_token_validator: ExternalTokenValidator
        if external_token_validator is None:

            def configured_token_validator(access_token: str) -> None:
                client = self._client_factory(
                    self._settings.kite_static_ip,
                    self._settings.kite_http_proxy,
                )
                try:
                    validate_access_token(
                        client,
                        self._settings.kite_api_key,
                        access_token,
                        expected_user_id=self._settings.kite_user_id,
                    )
                finally:
                    self._close_client(client)

            self._external_token_validator = configured_token_validator
        else:
            self._external_token_validator = external_token_validator
        self._clock = clock
        self._lock = Lock()
        self._attempts: dict[str, _LoginAttempt] = {}
        self._last_start_at: int | None = None

    def start(self, trading_date: str) -> LoginProgress:
        """Submit env credentials and create an opaque TOTP attempt."""
        with self._lock:
            self._remove_expired()
            existing = next(iter(self._attempts.values()), None)
            if existing is not None:
                raise KiteLoginError("a login attempt is already in progress")

            current_time = self._clock()
            if (
                self._last_start_at is not None
                and current_time - self._last_start_at < LOGIN_START_COOLDOWN_MS
            ):
                raise KiteLoginError("login start is temporarily rate limited")
            self._last_start_at = current_time

            access_token = self._external_token_fetcher()
            if access_token:
                try:
                    self._external_token_validator(access_token)
                except Exception as exc:
                    raise ExternalTokenError(
                        "external token service returned an unusable token"
                    ) from exc
                attempt = _LoginAttempt(
                    attempt_id=uuid4().hex,
                    step=LoginStep.AWAITING_RISK_FREE_RATE,
                    method=LoginMethod.SHARED_SESSION,
                    trading_date=trading_date,
                    expires_at=current_time + LOGIN_ATTEMPT_TTL_MS,
                    access_token=access_token,
                )
                self._attempts = {**self._attempts, attempt.attempt_id: attempt}
                return attempt.progress()

            user_id = self._settings.kite_user_id
            password = self._settings.kite_password
            if not user_id or not password:
                raise KiteLoginError("KITE_USER_ID and KITE_PASSWORD must be configured")

            client = self._client_factory(
                self._settings.kite_static_ip,
                self._settings.kite_http_proxy,
            )
            try:
                challenge = begin_login(client, user_id, password)
            except Exception:
                self._close_client(client)
                raise

            attempt = _LoginAttempt(
                attempt_id=uuid4().hex,
                step=LoginStep.AWAITING_TOTP,
                method=LoginMethod.LOCAL_CREDENTIALS,
                trading_date=trading_date,
                expires_at=current_time + LOGIN_ATTEMPT_TTL_MS,
                client=client,
                challenge=challenge,
            )
            self._attempts = {**self._attempts, attempt.attempt_id: attempt}
            return attempt.progress()

    def submit_totp(self, attempt_id: str, totp: str) -> LoginProgress:
        """Complete two-factor authentication and advance to rate confirmation."""
        with self._lock:
            attempt = self._get_attempt(attempt_id)
            if attempt.step is not LoginStep.AWAITING_TOTP:
                raise KiteLoginError("login attempt is not awaiting TOTP")
            if attempt.client is None or attempt.challenge is None:
                raise KiteLoginError("login attempt has no TOTP challenge")
            try:
                request_token = complete_totp(
                    attempt.client,
                    self._settings.kite_api_key,
                    self._settings.kite_user_id,
                    attempt.challenge,
                    totp,
                )
            except Exception:
                self._consume(attempt_id)
                raise
            updated = replace(
                attempt,
                step=LoginStep.AWAITING_RISK_FREE_RATE,
                request_token=request_token,
            )
            self._attempts = {**self._attempts, attempt_id: updated}
            return updated.progress()

    def complete(self, attempt_id: str, risk_free_rate: float) -> SessionState:
        """Exchange the request token, persist the daily session, and consume the attempt."""
        validated_rate = validate_risk_free_rate(risk_free_rate)

        with self._lock:
            attempt = self._get_attempt(attempt_id)
            if attempt.step is not LoginStep.AWAITING_RISK_FREE_RATE:
                raise KiteLoginError("login attempt is not awaiting risk_free_rate")
            if not attempt.access_token and not attempt.request_token:
                raise KiteLoginError("login attempt has no request token")

            try:
                access_token = attempt.access_token
                if access_token is None:
                    if attempt.client is None or attempt.request_token is None:
                        raise KiteLoginError("login attempt cannot exchange a request token")
                    access_token = exchange_request_token(
                        attempt.client,
                        self._settings.kite_api_key,
                        self._settings.kite_api_secret,
                        attempt.request_token,
                    )
                timestamp = self._clock()
                state = SessionState(
                    trading_date=attempt.trading_date,
                    access_token=access_token,
                    risk_free_rate=validated_rate,
                    access_token_at=timestamp,
                    started_at=timestamp,
                )
                save_session(self._settings.state_dir, state)
            except Exception:
                self._consume(attempt_id)
                raise
            self._consume(attempt_id)
            return state

    def cancel(self, attempt_id: str) -> None:
        with self._lock:
            self._remove_expired()
            if attempt_id not in self._attempts:
                return
            self._consume(attempt_id)

    def close(self) -> None:
        with self._lock:
            attempts = tuple(self._attempts.values())
            self._attempts = {}
            for attempt in attempts:
                self._close_client(attempt.client)

    def _get_attempt(self, attempt_id: str) -> _LoginAttempt:
        self._remove_expired()
        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise KiteLoginError("login attempt not found or expired")
        return attempt

    def _remove_expired(self) -> None:
        current_time = self._clock()
        active_attempts: dict[str, _LoginAttempt] = {}
        for attempt_id, attempt in self._attempts.items():
            if attempt.expires_at <= current_time:
                self._close_client(attempt.client)
            else:
                active_attempts = {**active_attempts, attempt_id: attempt}
        self._attempts = active_attempts

    def _consume(self, attempt_id: str) -> None:
        attempt = self._attempts[attempt_id]
        self._attempts = {
            existing_id: existing
            for existing_id, existing in self._attempts.items()
            if existing_id != attempt_id
        }
        self._close_client(attempt.client)

    @staticmethod
    def _close_client(client: HttpClient | None) -> None:
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.exception("failed to close Kite login client")
