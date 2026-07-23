"""Daily session state (access_token + risk-free rate) persistence and resume.

The two values that are *not* in ``.env`` -- the Kite ``access_token`` and the daily
risk-free rate -- are held in a small JSON file so a mid-day restart can reuse them
without re-fetching (docs/60-operations/session-state.md).

    MARKET_DATA/_state/session-<YYYY-MM-DD>.json
"""

from __future__ import annotations

import json
import logging
import math
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def now_ms() -> int:
    """Current Unix epoch time in milliseconds (UTC)."""
    return int(time.time() * 1000)


def is_session_capture_ready(state: object) -> bool:
    """Compatibility-safe readiness check for persisted or injected session objects."""
    explicit = getattr(state, "capture_ready", None)
    if explicit is not None:
        return bool(explicit)
    return bool(
        getattr(state, "access_token", None)
        and getattr(state, "risk_free_rate", None) is not None
    )


@dataclass(frozen=True)
class SessionState:
    """One trading day's interactive login values."""

    trading_date: str  # IST trading date, "YYYY-MM-DD"
    access_token: str
    risk_free_rate: float | None  # daily risk-free rate (decimal), fetched from calspread
    access_token_at: int  # ms
    started_at: int  # ms
    risk_free_rate_as_of: str | None = None

    def __post_init__(self) -> None:
        if self.risk_free_rate is not None and (
            not math.isfinite(float(self.risk_free_rate))
            or not 0 <= self.risk_free_rate <= 1
        ):
            raise ValueError("risk-free rate must be a decimal between 0 and 1")
        if self.risk_free_rate_as_of is None and self.risk_free_rate is not None:
            object.__setattr__(self, "risk_free_rate_as_of", self.trading_date)

    @property
    def capture_ready(self) -> bool:
        return bool(self.access_token and self.risk_free_rate is not None)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SessionState:
        return cls(
            trading_date=data["trading_date"],
            access_token=data["access_token"],
            risk_free_rate=(
                float(data["risk_free_rate"])
                if data.get("risk_free_rate") is not None
                else None
            ),
            access_token_at=int(data["access_token_at"]),
            started_at=int(data["started_at"]),
            risk_free_rate_as_of=(
                data.get("risk_free_rate_as_of") or data.get("trading_date")
            ),
        )


def session_path(state_dir: str | os.PathLike[str], trading_date: str) -> Path:
    return Path(state_dir) / f"session-{trading_date}.json"


def save_session(state_dir: str | os.PathLike[str], state: SessionState) -> Path:
    """Write the session state atomically (temp file + rename)."""
    path = session_path(state_dir, state.trading_date)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
            json.dump(state.to_dict(), temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        tmp.replace(path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    path.chmod(0o600)
    return path


def load_session(state_dir: str | os.PathLike[str], trading_date: str) -> SessionState | None:
    """Load today's session state, or ``None`` if it does not exist."""
    path = session_path(state_dir, trading_date)
    if not path.exists():
        return None
    try:
        return SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        quarantine = path.with_name(f"{path.name}.corrupt-{now_ms()}")
        path.replace(quarantine)
        logger.error("quarantined invalid session state (%s)", type(exc).__name__)
        return None


def invalidate_session(
    state_dir: str | os.PathLike[str],
    trading_date: str,
    expected_access_token: str,
) -> bool:
    """Quarantine today's exact token while retaining its risk-free-rate provenance.

    The active filename is removed atomically so the next automation tick sees no
    session. The invalidated record remains available to ``latest_stored_risk_free_rate``
    but is excluded from token reuse.
    """
    path = session_path(state_dir, trading_date)
    if not path.exists():
        return False
    try:
        state = SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    if not secrets.compare_digest(state.access_token, expected_access_token):
        return False

    invalidated = path.with_name(
        f"session-{trading_date}.invalidated-{now_ms()}.json"
    )
    try:
        path.replace(invalidated)
        invalidated.chmod(0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileNotFoundError:
        return False
    return True


def load_latest_session_before(
    state_dir: str | os.PathLike[str], trading_date: str
) -> SessionState | None:
    """Load the newest valid persisted session before ``trading_date``."""
    candidates: list[SessionState] = []
    for path in Path(state_dir).glob("session-*.json"):
        if ".invalidated-" in path.name:
            continue
        try:
            state = SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if date.fromisoformat(state.trading_date) < date.fromisoformat(trading_date):
                candidates = [*candidates, state]
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.trading_date)


def latest_stored_risk_free_rate(
    state_dir: str | os.PathLike[str], trading_date: str
) -> tuple[float | None, str | None]:
    """Return the newest prior stored ``(risk_free_rate, as_of)`` on/before ``trading_date``.

    Used only as a fallback when the daily broker fetch and the env value are both
    unavailable. There is no freshness/expiry rule — the rate is fetched fresh each day.
    """
    target_date = date.fromisoformat(trading_date)
    candidates: list[SessionState] = []
    for path in sorted(Path(state_dir).glob("session-*.json"), reverse=True):
        try:
            state = SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
            as_of = date.fromisoformat(state.risk_free_rate_as_of or state.trading_date)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if state.risk_free_rate is None or as_of > target_date:
            continue
        candidates = [*candidates, state]

    if not candidates:
        return None, None
    latest = max(
        candidates,
        key=lambda item: date.fromisoformat(item.risk_free_rate_as_of or item.trading_date),
    )
    return latest.risk_free_rate, (latest.risk_free_rate_as_of or latest.trading_date)
