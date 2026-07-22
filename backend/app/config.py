"""Typed application configuration (pydantic-settings).

Reads a ``.env`` file. ``KITE_API_KEY``, ``KITE_API_SECRET``, ``MARKET_DATA_PATH``,
``ARCHIVE_DATA_PATH``, ``HTTP_PORT``, and ``FRONTEND_URL`` are required; other settings
have sensible defaults.

The daily ``access_token`` and the 10-yr bond yield are deliberately *not* here --
they are entered at login and kept in session state (see docs/60-operations/
session-state.md), because they change every day.

Derived paths (``indices_dir`` etc.) are rooted at ``MARKET_DATA_PATH`` and match the
storage layout in docs/20-data-and-storage/storage-layout.md.
"""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Locked index universe (docs/90-decisions/decisions-and-open-questions.md #9).
DEFAULT_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]


class Settings(BaseSettings):
    """Application settings loaded from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- required ---
    kite_api_key: str = Field(..., description="Kite Connect app key")
    kite_api_secret: str = Field(..., description="Kite Connect app secret")
    market_data_path: Path = Field(..., description="SSD root for live captured data")
    archive_data_path: Path = Field(..., description="HDD root for verified zstd archives")

    # --- automated-login credentials (seeded from env; needed only to log in) ---
    # algo_engine stores these encrypted in Postgres; here they come from the env so a
    # single `md-login` run can complete the flow without a browser.
    kite_user_id: str | None = Field(default=None, description="Zerodha user id, e.g. AB1234")
    kite_password: str | None = Field(default=None, description="Zerodha login password")
    risk_free_rate: float | None = Field(
        default=None,
        description="10-yr bond yield (decimal) stamped into headers; prompted if unset.",
    )

    # --- existing-session broker (backend-only; checked before local credentials) ---
    kite_token_broker_url: AnyHttpUrl | None = Field(
        default=None,
        description="HTTPS endpoint that returns an existing Kite access token",
    )
    kite_token_broker_passcode: SecretStr | None = Field(
        default=None,
        description="Backend-only x-token-passcode for the Kite token broker",
    )

    # --- internal release drain lease (backend-only) ---
    release_maintenance_token: SecretStr | None = Field(
        default=None,
        description="Secret header value for the internal release-maintenance API",
    )
    release_maintenance_ttl_seconds: int = Field(
        default=900,
        ge=30,
        le=900,
        description="Bounded lifetime for a persisted release-maintenance lease",
    )

    # --- egress control (Kite requires a whitelisted static IP from Apr 2026) ---
    # Bind outbound Kite calls to this source address (the host's static IP), and/or
    # route them through a proxy that egresses from the static IP.
    kite_static_ip: str | None = Field(
        default=None, description="Local source IP to bind outbound Kite requests to"
    )
    kite_http_proxy: str | None = Field(
        default=None, description="Optional proxy URL for Kite egress (e.g. http://host:port)"
    )

    # --- networking (ports come ONLY from the environment; no hardcoded defaults) ---
    # ``http_port`` is required so the backend port is configured entirely via .env.
    http_host: str = Field(default="127.0.0.1", description="Bind host for the backend")
    http_port: int = Field(..., ge=1, le=65535, description="Backend HTTP/WS port (from env)")
    # Frontend origin(s) for CORS + allowed WebSocket origins. Contains the frontend
    # port, so it too is env-only (comma-separate for multiple origins).
    frontend_url: str = Field(..., description="Frontend origin(s) for CORS")

    # --- browser operator authentication ---
    # The long-lived token is exchanged for a short-lived opaque HttpOnly cookie.
    operator_api_token: SecretStr = Field(
        ...,
        description="Backend-only operator unlock token (32-256 characters)",
    )
    operator_session_ttl_seconds: int = Field(default=3_600, ge=300, le=43_200)
    operator_login_max_attempts: int = Field(default=5, ge=1, le=20)
    operator_login_window_seconds: int = Field(default=60, ge=10, le=3_600)
    operator_cookie_secure: bool = Field(
        default=False,
        description="Require HTTPS when sending the operator session cookie",
    )

    # --- optional, with locked defaults ---
    # NoDecode: keep pydantic-settings from JSON-decoding this list field so the
    # comma-separated env value (``INDICES=NIFTY,BANKNIFTY,...``) reaches the validator.
    indices: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(DEFAULT_INDICES))
    stock_universe: str = Field(default="all", description="'all' or a comma allow-list")
    capture_hz: int = Field(default=1, ge=1, description="Snapshot cadence (Hz)")
    zstd_level: int = Field(default=17, ge=1, le=22, description="EOD compression level")
    auth_poll_start: str = Field(default="08:30", description="Broker polling start (IST)")
    auth_poll_end: str = Field(default="09:00", description="Broker polling stop (IST)")
    auth_poll_interval_seconds: int = Field(
        default=60,
        ge=5,
        le=1_800,
        description="Seconds between shared-token checks inside the auth window",
    )
    market_open: str = Field(default="09:00", description="Capture start (IST, HH:MM)")
    market_close: str = Field(default="15:30", description="Session close (IST, HH:MM)")
    timezone: str = Field(default="Asia/Kolkata", description="Exchange timezone")
    log_level: str = Field(default="INFO")

    @field_validator("risk_free_rate", mode="before")
    @classmethod
    def _blank_optional_float_is_none(cls, value: object) -> object:
        """Treat an empty optional env value as unset instead of an invalid float."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("release_maintenance_token")
    @classmethod
    def _release_maintenance_token_must_not_be_blank(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is None:
            return None
        token_length = len(value.get_secret_value().strip())
        if not 32 <= token_length <= 256:
            raise ValueError("RELEASE_MAINTENANCE_TOKEN must contain 32 to 256 characters")
        return value

    @field_validator("operator_api_token")
    @classmethod
    def _operator_api_token_must_be_strong(cls, value: SecretStr) -> SecretStr:
        token_length = len(value.get_secret_value().strip())
        if not 32 <= token_length <= 256:
            raise ValueError("OPERATOR_API_TOKEN must contain 32 to 256 characters")
        return value

    @field_validator("indices", mode="before")
    @classmethod
    def _split_indices(cls, value: object) -> object:
        """Allow a comma-separated string (from .env) or a list."""
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _validate_token_broker(self) -> Settings:
        url = self.kite_token_broker_url
        passcode = self.kite_token_broker_passcode
        if (url is None) != (passcode is None):
            raise ValueError(
                "KITE_TOKEN_BROKER_URL and KITE_TOKEN_BROKER_PASSCODE must be set together"
            )
        if url is None:
            return self
        if not passcode or not passcode.get_secret_value().strip():
            raise ValueError("KITE_TOKEN_BROKER_PASSCODE must not be blank")
        if not self.kite_user_id or not self.kite_user_id.strip():
            raise ValueError("KITE_USER_ID is required when the shared token broker is enabled")
        if (
            url.scheme != "https"
            or url.host != "calspread.online"
            or url.port not in (None, 443)
            or url.path != "/api/kite/token"
            or url.query is not None
            or url.fragment is not None
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError("KITE_TOKEN_BROKER_URL must be the approved HTTPS token endpoint")
        return self

    @model_validator(mode="after")
    def _validate_storage_roots(self) -> Settings:
        live_root = self.market_data_path.resolve(strict=False)
        archive_root = self.archive_data_path.resolve(strict=False)
        if live_root == archive_root:
            raise ValueError("MARKET_DATA_PATH and ARCHIVE_DATA_PATH must differ")
        return self

    @model_validator(mode="after")
    def _validate_daily_schedule(self) -> Settings:
        def parse(value: str) -> time:
            try:
                hour_text, minute_text = value.split(":")
                if len(hour_text) != 2 or len(minute_text) != 2:
                    raise ValueError
                return time(int(hour_text), int(minute_text))
            except (TypeError, ValueError) as exc:
                raise ValueError("daily schedule values must use HH:MM") from exc

        auth_start = parse(self.auth_poll_start)
        auth_end = parse(self.auth_poll_end)
        market_open = parse(self.market_open)
        market_close = parse(self.market_close)
        if not auth_start < auth_end <= market_open < market_close:
            raise ValueError(
                "daily schedule must satisfy AUTH_POLL_START < AUTH_POLL_END "
                "<= MARKET_OPEN < MARKET_CLOSE"
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        """Allowed browser origins, parsed from ``frontend_url`` (comma-separated)."""
        return [o.strip() for o in self.frontend_url.split(",") if o.strip()]

    # --- derived storage paths (docs/20-data-and-storage/storage-layout.md) ---
    @property
    def indices_dir(self) -> Path:
        return self.market_data_path / "INDICES"

    @property
    def stocks_dir(self) -> Path:
        return self.market_data_path / "STOCKS"

    @property
    def indices_his_dir(self) -> Path:
        return self.market_data_path / "INDICES_HIS"

    @property
    def stocks_his_dir(self) -> Path:
        return self.market_data_path / "STOCKS_HIS"

    @property
    def instruments_dir(self) -> Path:
        return self.market_data_path / "_instruments"

    @property
    def state_dir(self) -> Path:
        return self.market_data_path / "_state"

    @property
    def meta_dir(self) -> Path:
        return self.market_data_path / "_meta"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()  # type: ignore[call-arg]
