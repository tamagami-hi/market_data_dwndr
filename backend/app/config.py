"""Typed application configuration (pydantic-settings).

Reads a ``.env`` file. ``KITE_API_KEY``, ``KITE_API_SECRET``, ``MARKET_DATA_PATH``,
``HTTP_PORT``, and ``FRONTEND_URL`` are required; other settings have sensible defaults.

The daily ``access_token`` and the 10-yr bond yield are deliberately *not* here --
they are entered at login and kept in session state (see docs/60-operations/
session-state.md), because they change every day.

Derived paths (``indices_dir`` etc.) are rooted at ``MARKET_DATA_PATH`` and match the
storage layout in docs/20-data-and-storage/storage-layout.md.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
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
    market_data_path: Path = Field(..., description="Output root for captured data")

    # --- automated-login credentials (seeded from env; needed only to log in) ---
    # algo_engine stores these encrypted in Postgres; here they come from the env so a
    # single `md-login` run can complete the flow without a browser.
    kite_user_id: str | None = Field(default=None, description="Zerodha user id, e.g. AB1234")
    kite_password: str | None = Field(default=None, description="Zerodha login password")
    kite_totp_secret: str | None = Field(
        default=None,
        description="Base32 TOTP secret. If unset, the TOTP is prompted from the terminal.",
    )
    risk_free_rate: float | None = Field(
        default=None,
        description="10-yr bond yield (decimal) stamped into headers; prompted if unset.",
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
    http_host: str = Field(default="0.0.0.0", description="Bind host for the backend")
    http_port: int = Field(..., ge=1, le=65535, description="Backend HTTP/WS port (from env)")
    # Frontend origin(s) for CORS + allowed WebSocket origins. Contains the frontend
    # port, so it too is env-only (comma-separate for multiple origins).
    frontend_url: str = Field(
        ..., description="Frontend origin(s) for CORS"
    )

    # --- optional, with locked defaults ---
    # NoDecode: keep pydantic-settings from JSON-decoding this list field so the
    # comma-separated env value (``INDICES=NIFTY,BANKNIFTY,...``) reaches the validator.
    indices: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_INDICES)
    )
    stock_universe: str = Field(default="all", description="'all' or a comma allow-list")
    capture_hz: int = Field(default=1, ge=1, description="Snapshot cadence (Hz)")
    zstd_level: int = Field(default=17, ge=1, le=22, description="EOD compression level")
    market_open: str = Field(default="09:15", description="Session open (IST, HH:MM)")
    market_close: str = Field(default="15:30", description="Session close (IST, HH:MM)")
    timezone: str = Field(default="Asia/Kolkata", description="Exchange timezone")
    log_level: str = Field(default="INFO")

    @field_validator("indices", mode="before")
    @classmethod
    def _split_indices(cls, value: object) -> object:
        """Allow a comma-separated string (from .env) or a list."""
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        return value

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
