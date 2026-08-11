"""Typed application configuration (pydantic-settings).

Reads a ``.env`` file. ``KITE_API_KEY``, ``KITE_API_SECRET``, ``MARKET_DATA_PATH``,
``ARCHIVE_DATA_PATH``, ``HTTP_PORT``, and ``FRONTEND_URL`` are required; other settings
have sensible defaults.

The daily ``access_token`` and the risk-free rate are deliberately *not* here --
the token is obtained at login and the risk-free rate is fetched daily from the
calspread broker (``RISK_FREE_RATE`` in the env is the fallback), because they change
every day.

Derived paths (``indices_dir`` etc.) are rooted at ``MARKET_DATA_PATH`` and match the
storage layout in docs/20-data-and-storage/storage-layout.md.
"""

from __future__ import annotations

from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Locked index universe (docs/90-decisions/decisions-and-open-questions.md #9).
DEFAULT_INDICES = [
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "SENSEX",
    "BANKEX",
]


def _parse_hhmm(value: str) -> time:
    """Parse a strict ``HH:MM`` schedule value (used by validation and session sizing)."""
    try:
        hour_text, minute_text = str(value).split(":")
        if len(hour_text) != 2 or len(minute_text) != 2:
            raise ValueError
        return time(int(hour_text), int(minute_text))
    except (TypeError, ValueError) as exc:
        raise ValueError("daily schedule values must use HH:MM") from exc


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
    stats_data_path: Path | None = Field(
        default=None,
        description=(
            "Directory for persisted monitor statistics JSON (compression history + "
            "daily capture snapshots). Defaults to MARKET_DATA_PATH/_state/stats when unset."
        ),
    )

    # --- automated-login credentials (seeded from env; needed only to log in) ---
    # algo_engine stores these encrypted in Postgres; here they come from the env so a
    # single `md-login` run can complete the flow without a browser.
    kite_user_id: str | None = Field(default=None, description="Zerodha user id, e.g. AB1234")
    kite_password: str | None = Field(default=None, description="Zerodha login password")
    risk_free_rate: float | None = Field(
        default=None,
        description=(
            "Fallback risk-free rate (decimal) stamped into headers when the rate "
            "broker is unavailable; the daily value is normally fetched from calspread."
        ),
    )

    # --- existing-session broker (backend-only; checked before local credentials) ---
    kite_token_broker_url: AnyHttpUrl | None = Field(
        default=None,
        description="HTTPS endpoint that returns an existing Kite access token",
    )
    kite_token_broker_passcode: SecretStr | None = Field(
        default=None,
        description="Backend-only x-token-passcode for the Kite token + risk-free-rate brokers",
    )
    kite_rate_broker_url: AnyHttpUrl | None = Field(
        default=None,
        description=(
            "HTTPS endpoint returning the daily risk-free rate as a percent "
            "(reuses x-token-passcode); e.g. https://calspread.online/api/rf"
        ),
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

    # --- networking (ports come ONLY from the environment; no hardcoded defaults) ---
    # ``http_port`` is required so the backend port is configured entirely via .env.
    http_host: str = Field(default="127.0.0.1", description="Bind host for the backend")
    http_port: int = Field(..., ge=1, le=65535, description="Backend HTTP/WS port (from env)")
    # Frontend origin(s) for CORS + allowed WebSocket origins. Contains the frontend
    # port, so it too is env-only (comma-separate for multiple origins).
    frontend_url: str = Field(..., description="Frontend origin(s) for CORS")

    # --- optional, with locked defaults ---
    # NoDecode: keep pydantic-settings from JSON-decoding this list field so the
    # comma-separated env value (``INDICES=NIFTY,BANKNIFTY,...``) reaches the validator.
    indices: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(DEFAULT_INDICES))
    market_holidays: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Comma-separated ISO dates when the exchange is closed",
    )
    stock_universe: str = Field(default="all", description="'all' or a comma allow-list")
    capture_hz: int = Field(default=1, ge=1, description="Snapshot cadence (Hz)")
    zstd_level: int = Field(default=17, ge=1, le=22, description="EOD compression level")
    zstd_threads: int = Field(
        default=6,
        ge=1,
        le=6,
        description="Worker threads for EOD zstd compression (capped 1-6)",
    )
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
    timezone: str = Field(
        default="Asia/Kolkata",
        description="Exchange timezone. Accepts TIMEZONE or MARKET_TIMEZONE.",
        validation_alias=AliasChoices("TIMEZONE", "MARKET_TIMEZONE"),
    )
    expected_frames_override: int | None = Field(
        default=None,
        ge=1,
        validation_alias="EXPECTED_FRAMES_PER_SESSION",
        description=(
            "Optional hard override for the full-session frame baseline. Leave unset: "
            "the baseline is normally DERIVED from MARKET_OPEN..MARKET_CLOSE and "
            "CAPTURE_HZ (see the expected_frames_per_session property), so changing the "
            "session window automatically corrects the monitor's frame-loss metric."
        ),
    )
    capture_stale_seconds: float = Field(
        default=5.0,
        gt=0,
        le=300,
        description=(
            "Seconds without fresh (content-changing) ticks before the live feed is "
            "flagged stale/degraded and a self-driven ticker reconnect is triggered. "
            "Tunable via CAPTURE_STALE_SECONDS in .env."
        ),
    )
    capture_suppress_stale_writes: bool = Field(
        default=True,
        description=(
            "Do not write a frame when the feed is stale (see CAPTURE_STALE_SECONDS). "
            "A stale grid second carries duplicate last-known values, so writing it "
            "fabricates market data that never traded: an honest hole in the file is "
            "recoverable, a silently frozen frame is not. Set false only to reproduce "
            "the legacy behaviour of writing frozen frames. Tunable via "
            "CAPTURE_SUPPRESS_STALE_WRITES."
        ),
    )
    capture_stale_exit_seconds: float = Field(
        default=60.0,
        ge=0,
        le=3_600,
        description=(
            "Seconds of CONTINUOUS staleness, while the market is trading, before the "
            "process exits so Docker restarts it with a clean session + fresh token "
            "(restart-first recovery). A brief flicker of ticks does not reset the spell, "
            "but a feed that is healthy right now is never restarted. 0 = disabled. "
            "Tunable via CAPTURE_STALE_EXIT_SECONDS."
        ),
    )
    capture_stale_exit_max_restarts: int = Field(
        default=3,
        ge=0,
        le=50,
        description=(
            "Restart escalations for one trading date after which the engine logs at "
            "CRITICAL that the nominal budget is spent. It does NOT cap recovery: while "
            "the market session is open capture keeps restarting for a dead feed, because "
            "a process that has stopped trying to fetch is worse than a recoverable gap. "
            "0 = never warn. Tunable via CAPTURE_STALE_EXIT_MAX_RESTARTS."
        ),
    )
    capture_stale_recovery_confirm_seconds: float = Field(
        default=15.0,
        ge=0,
        le=600,
        description=(
            "Seconds of SUSTAINED fresh ticks required to declare a stale spell over. "
            "Guards the restart deadline against flickers: on 2026-08-06 a single fresh "
            "second reset the old recovery ladder and disarmed escalation for another "
            "hour. Tunable via CAPTURE_STALE_RECOVERY_CONFIRM_SECONDS."
        ),
    )
    capture_recovery_arm_delay_seconds: float = Field(
        default=300.0,
        ge=0,
        le=7_200,
        description=(
            "Grace period after MARKET_OPEN before staleness is treated as a fault. "
            "Capture starts at MARKET_OPEN but the exchange's continuous session begins "
            "later (MARKET_OPEN=09:10 vs NSE 09:15), and no ticks before then is normal — "
            "without this grace the process would exit every minute of every pre-open. "
            "Tunable via CAPTURE_RECOVERY_ARM_DELAY_SECONDS."
        ),
    )
    # --- session-oriented timing (see app/ops/sessions.py) --------------------------
    # Each of these falls back to MARKET_OPEN/MARKET_CLOSE when unset, so an existing
    # deployment keeps its exact schedule until the session block is added to the env.
    # Several artifacts sharing a session share ONE piece of configuration: there is
    # deliberately no NIFTY_CLOSE / STOCK_FNO_CLOSE style per-artifact setting.
    bootstrap_time: str | None = Field(
        default=None,
        description=(
            "When the process may begin preparing a session (instrument masters, chains, "
            "subscriptions) before any frame is expected. Informational: capture "
            "activation is governed by the session windows below. BOOTSTRAP_TIME."
        ),
    )
    equity_deriv_open: str | None = Field(
        default=None,
        description="Equity-derivatives continuous session start (IST HH:MM). EQUITY_DERIV_OPEN.",
    )
    equity_deriv_close: str | None = Field(
        default=None,
        description="Equity-derivatives session close (IST HH:MM). EQUITY_DERIV_CLOSE.",
    )
    equity_deriv_preopen_start: str | None = Field(
        default=None,
        description="Equity-derivatives pre-open start (IST HH:MM). EQUITY_DERIV_PREOPEN_START.",
    )
    equity_deriv_preopen_end: str | None = Field(
        default=None,
        description="Equity-derivatives pre-open end (IST HH:MM). EQUITY_DERIV_PREOPEN_END.",
    )
    equity_deriv_capture_preopen: bool = Field(
        default=False,
        description=(
            "Persist the equity-derivatives pre-open auction. Pre-open is a POLICY, not "
            "an assumption: when false the pre-open window schedules no frames and its "
            "silence is not data loss. EQUITY_DERIV_CAPTURE_PREOPEN."
        ),
    )
    equity_deriv_enabled: bool = Field(
        default=True,
        description=(
            "Explicitly disable equity-derivatives capture. A disabled session schedules "
            "no seconds, so its silence is 'not expected data' rather than data loss — "
            "which is what separates maintenance from an outage. EQUITY_DERIV_ENABLED."
        ),
    )
    equity_cash_open: str | None = Field(
        default=None,
        description="Equity-cash session start (IST HH:MM). EQUITY_CASH_OPEN.",
    )
    equity_cash_close: str | None = Field(
        default=None,
        description="Equity-cash session close (IST HH:MM). EQUITY_CASH_CLOSE.",
    )
    equity_cash_preopen_start: str | None = Field(
        default=None,
        description="Equity-cash pre-open start (IST HH:MM). EQUITY_CASH_PREOPEN_START.",
    )
    equity_cash_preopen_end: str | None = Field(
        default=None,
        description="Equity-cash pre-open end (IST HH:MM). EQUITY_CASH_PREOPEN_END.",
    )
    equity_cash_capture_preopen: bool = Field(
        default=False,
        description="Persist the equity-cash pre-open auction. EQUITY_CASH_CAPTURE_PREOPEN.",
    )
    equity_cash_enabled: bool = Field(
        default=True,
        description="Explicitly disable equity-cash capture. EQUITY_CASH_ENABLED.",
    )
    broker_subscription_limit: int = Field(
        default=3_000,
        ge=1,
        description=(
            "Instruments the broker accepts per websocket connection (Kite Connect: 3000). "
            "Used to compute subscription headroom at bootstrap. BROKER_SUBSCRIPTION_LIMIT."
        ),
    )
    broker_subscription_safety_margin_pct: float = Field(
        default=10.0,
        ge=0,
        le=90,
        description=(
            "Headroom held below BROKER_SUBSCRIPTION_LIMIT. Crossing the resulting safe "
            "threshold is reported loudly rather than silently risking a rejected "
            "subscribe (which surfaces only as a dead feed). "
            "BROKER_SUBSCRIPTION_SAFETY_MARGIN_PCT."
        ),
    )
    broker_max_connections: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Websocket connections the broker allows per API key (Kite Connect: 3). Bounds "
            "how far subscription sharding could ever scale. BROKER_MAX_CONNECTIONS."
        ),
    )
    capture_start_time: str | None = Field(
        default=None,
        description=(
            "When the capture process activates: connects, subscribes, and starts the 1 Hz "
            "grid. This is deliberately EARLIER than the market session open so the socket "
            "and subscriptions are established before the first print. It is NOT a market "
            "time and nothing is owed before the session opens. Defaults to MARKET_OPEN. "
            "CAPTURE_START_TIME."
        ),
    )
    capture_end_time: str | None = Field(
        default=None,
        description=(
            "When the capture process deactivates. Defaults to MARKET_CLOSE. Set later than "
            "the session close to keep capturing a post-close tail — those extra seconds are "
            "reported as unscheduled, never as data loss. CAPTURE_END_TIME."
        ),
    )
    indices_fno_enabled: bool = Field(
        default=True,
        description=(
            "Capture the consolidated index-F&O dataset: index futures (3 nearest "
            "expiries) plus each index's spot, for every index in INDICES, on the shared "
            "1 Hz grid in one file. Separate from the per-index option files and from the "
            "stock F&O file, so no existing binary contract changes. INDICES_FNO_ENABLED."
        ),
    )
    log_level: str = Field(default="INFO")

    @field_validator(
        "bootstrap_time",
        "capture_start_time",
        "capture_end_time",
        "equity_deriv_open",
        "equity_deriv_close",
        "equity_deriv_preopen_start",
        "equity_deriv_preopen_end",
        "equity_cash_open",
        "equity_cash_close",
        "equity_cash_preopen_start",
        "equity_cash_preopen_end",
        mode="before",
    )
    @classmethod
    def _blank_session_time_is_unset(cls, value: object) -> object:
        """An empty session time means 'inherit the legacy MARKET_OPEN/MARKET_CLOSE'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("capture_stale_seconds", mode="before")
    @classmethod
    def _blank_stale_seconds_is_default(cls, value: object) -> object:
        """Treat an empty CAPTURE_STALE_SECONDS env value as the default (5s)."""
        if isinstance(value, str) and not value.strip():
            return 5.0
        return value

    @field_validator(
        "capture_stale_exit_seconds",
        "capture_stale_exit_max_restarts",
        "capture_stale_recovery_confirm_seconds",
        "capture_recovery_arm_delay_seconds",
        mode="before",
    )
    @classmethod
    def _blank_numeric_recovery_is_default(cls, value: object, info) -> object:
        """Treat an empty recovery-tuning env value as unset (use the field default)."""
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @field_validator(
        "expected_frames_override",
        mode="before",
    )
    @classmethod
    def _blank_frames_override_is_none(cls, value: object) -> object:
        """A blank EXPECTED_FRAMES_PER_SESSION means 'derive it', not 'invalid'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("risk_free_rate", mode="before")
    @classmethod
    def _blank_optional_float_is_none(cls, value: object) -> object:
        """Treat an empty optional env value as unset instead of an invalid float."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("stats_data_path", mode="before")
    @classmethod
    def _blank_optional_path_is_none(cls, value: object) -> object:
        """Treat an empty STATS_DATA_PATH env value as unset (use the default)."""
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

    @field_validator("indices", mode="before")
    @classmethod
    def _split_indices(cls, value: object) -> object:
        """Allow a comma-separated string (from .env) or a list."""
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        return value

    @field_validator("market_holidays", mode="before")
    @classmethod
    def _parse_market_holidays(cls, value: object) -> object:
        """Parse and normalize comma-separated ISO market-closure dates."""
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, (list, tuple, set)):
            return items
        normalized: set[str] = set()
        for item in items:
            text = str(item).strip()
            if not text:
                continue
            try:
                normalized.add(date.fromisoformat(text).isoformat())
            except ValueError as exc:
                raise ValueError("MARKET_HOLIDAYS must contain ISO dates (YYYY-MM-DD)") from exc
        return sorted(normalized)

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
        auth_start = _parse_hhmm(self.auth_poll_start)
        auth_end = _parse_hhmm(self.auth_poll_end)
        capture_start_str, capture_end_str = self.capture_window
        market_open = _parse_hhmm(capture_start_str)
        market_close = _parse_hhmm(capture_end_str)
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

    # --- derived session sizing -------------------------------------------- #
    @property
    def session_seconds(self) -> int:
        """Length of the capture window in seconds (``MARKET_OPEN``..``MARKET_CLOSE``).

        The schedule validator guarantees ``market_open < market_close``, so this is
        always positive and never wraps midnight.
        """
        opened = _parse_hhmm(self.market_open)
        closed = _parse_hhmm(self.market_close)
        return (closed.hour - opened.hour) * 3600 + (closed.minute - opened.minute) * 60

    @property
    def expected_frames_per_session(self) -> int:
        """Frames a complete session should produce — the monitor's loss baseline.

        DERIVED from the configured market window and snapshot cadence rather than
        hardcoded, so shortening/extending the session (e.g. a muhurat session, or
        MARKET_OPEN moved to 09:15) automatically yields the right baseline instead of
        silently reporting phantom frame loss against a stale 23,400.

        Default 09:00-15:30 at 1 Hz = 6h30m = 23,400 frames — the previous constant.
        ``EXPECTED_FRAMES_PER_SESSION`` still overrides it when set.
        """
        if self.expected_frames_override is not None:
            return self.expected_frames_override
        return max(1, self.session_seconds * self.capture_hz)

    # --- derived storage paths (docs/20-data-and-storage/storage-layout.md) ---
    @property
    def indices_dir(self) -> Path:
        return self.market_data_path / "INDICES"

    @property
    def stocks_dir(self) -> Path:
        return self.market_data_path / "STOCKS"

    @property
    def capture_window(self) -> tuple[str, str]:
        """``(start, end)`` HH:MM for when the capture PROCESS runs.

        Distinct from the market sessions in ``app/ops/sessions.py``, which decide when a
        frame is *owed*. The process deliberately starts earlier so the socket and
        subscriptions are live before the first print, and may run later to keep a
        post-close tail; neither affects the loss denominator.
        """
        return (
            self.capture_start_time or self.market_open,
            self.capture_end_time or self.market_close,
        )

    @property
    def indices_fno_dir(self) -> Path:
        return self.market_data_path / "INDICES_FnO"

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
    def stats_dir(self) -> Path:
        """Directory for persisted monitor statistics JSON.

        Uses ``STATS_DATA_PATH`` when set; otherwise defaults to the live
        ``_state/stats`` folder so existing deployments keep working unchanged.
        """
        if self.stats_data_path is not None:
            return self.stats_data_path
        return self.state_dir / "stats"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""
    return Settings()  # type: ignore[call-arg]
