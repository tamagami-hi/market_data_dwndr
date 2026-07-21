"""Typed application configuration (pydantic-settings).

Reads a ``.env`` file. Only ``KITE_API_KEY``, ``KITE_API_SECRET`` and
``MARKET_DATA_PATH`` are required; everything else has a sensible default.

The daily ``access_token`` and the 10-yr bond yield are deliberately *not* here --
they are entered at login and kept in session state (see docs/60-operations/
session-state.md), because they change every day.

Derived paths (``indices_dir`` etc.) are rooted at ``MARKET_DATA_PATH`` and match the
storage layout in docs/20-data-and-storage/storage-layout.md.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # --- optional, with locked defaults ---
    indices: list[str] = Field(default_factory=lambda: list(DEFAULT_INDICES))
    stock_universe: str = Field(default="all", description="'all' or a comma allow-list")
    capture_hz: int = Field(default=1, ge=1, description="Snapshot cadence (Hz)")
    zstd_level: int = Field(default=17, ge=1, le=22, description="EOD compression level")
    market_open: str = Field(default="09:15", description="Session open (IST, HH:MM)")
    market_close: str = Field(default="15:30", description="Session close (IST, HH:MM)")
    timezone: str = Field(default="Asia/Kolkata", description="Exchange timezone")
    log_level: str = Field(default="INFO")
    http_port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("indices", mode="before")
    @classmethod
    def _split_indices(cls, value: object) -> object:
        """Allow a comma-separated string (from .env) or a list."""
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        return value

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
