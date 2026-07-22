"""Daily session state (access_token + bond yield) persistence and resume.

The two values that are *not* in ``.env`` -- the Kite ``access_token`` and the 10-yr
bond yield entered at login -- are held in a small JSON file so a mid-day restart can
reuse them without re-prompting (docs/60-operations/session-state.md).

    MARKET_DATA/_state/session-<YYYY-MM-DD>.json
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


def now_ms() -> int:
    """Current Unix epoch time in milliseconds (UTC)."""
    return int(time.time() * 1000)


@dataclass
class SessionState:
    """One trading day's interactive login values."""

    trading_date: str  # IST trading date, "YYYY-MM-DD"
    access_token: str
    risk_free_rate: float  # 10-yr bond yield entered at login (decimal)
    access_token_at: int  # ms
    started_at: int  # ms

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SessionState:
        return cls(
            trading_date=data["trading_date"],
            access_token=data["access_token"],
            risk_free_rate=float(data["risk_free_rate"]),
            access_token_at=int(data["access_token_at"]),
            started_at=int(data["started_at"]),
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
        tmp.replace(path)
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
    return SessionState.from_dict(json.loads(path.read_text(encoding="utf-8")))
