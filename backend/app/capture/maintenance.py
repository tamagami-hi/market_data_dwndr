"""Persistent, bounded release-maintenance leases for capture draining."""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

LEASE_FILENAME = "release-maintenance.json"
LEASE_SCHEMA_VERSION = 1


class MaintenanceConflictError(Exception):
    """Raised when a valid maintenance lease is already active."""


class MaintenanceLeaseNotFoundError(Exception):
    """Raised when the requested active lease does not exist."""


class MaintenanceStateError(Exception):
    """Raised when persisted release state is corrupt and must fail closed."""


@dataclass(frozen=True)
class MaintenanceLease:
    """Immutable persisted maintenance lease."""

    lease_id: str
    acquired_at_ms: int
    expires_at_ms: int

    @property
    def expires_at(self) -> str:
        value = datetime.fromtimestamp(self.expires_at_ms / 1_000, tz=UTC)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def to_document(self) -> dict[str, str | int]:
        return {
            "schema_version": LEASE_SCHEMA_VERSION,
            "lease_id": self.lease_id,
            "acquired_at_ms": self.acquired_at_ms,
            "expires_at_ms": self.expires_at_ms,
        }

    @classmethod
    def from_document(cls, document: object) -> MaintenanceLease:
        if not isinstance(document, dict):
            raise ValueError("maintenance lease must be an object")
        if document.get("schema_version") != LEASE_SCHEMA_VERSION:
            raise ValueError("unsupported maintenance lease schema")

        lease_id = document.get("lease_id")
        acquired_at_ms = document.get("acquired_at_ms")
        expires_at_ms = document.get("expires_at_ms")
        if not isinstance(lease_id, str) or not lease_id or len(lease_id) > 128:
            raise ValueError("invalid maintenance lease id")
        if not _is_timestamp(acquired_at_ms) or not _is_timestamp(expires_at_ms):
            raise ValueError("invalid maintenance lease timestamps")
        if expires_at_ms <= acquired_at_ms:
            raise ValueError("maintenance lease expiry must follow acquisition")
        return cls(
            lease_id=lease_id,
            acquired_at_ms=acquired_at_ms,
            expires_at_ms=expires_at_ms,
        )


def _is_timestamp(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class MaintenanceLeaseStore:
    """Atomically persists a single short-lived lease under application state."""

    def __init__(
        self,
        state_dir: str | os.PathLike[str],
        *,
        ttl_seconds: int,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._state_dir = Path(state_dir)
        self._path = self._state_dir / LEASE_FILENAME
        self._ttl_ms = ttl_seconds * 1_000
        self._clock = clock or (lambda: time.time_ns() // 1_000_000)

    @property
    def path(self) -> Path:
        return self._path

    def active(self) -> MaintenanceLease | None:
        lease = self._load()
        if lease is None:
            return None
        if lease.expires_at_ms <= self._clock():
            self._remove()
            return None
        return lease

    def acquire(self) -> MaintenanceLease:
        if self.active() is not None:
            raise MaintenanceConflictError("release maintenance is already active")
        acquired_at_ms = self._clock()
        lease = MaintenanceLease(
            lease_id=secrets.token_urlsafe(32),
            acquired_at_ms=acquired_at_ms,
            expires_at_ms=acquired_at_ms + self._ttl_ms,
        )
        self._write(lease)
        return lease

    def release(self, lease_id: str) -> None:
        active = self.active()
        if active is None or not secrets.compare_digest(active.lease_id, lease_id):
            raise MaintenanceLeaseNotFoundError("active maintenance lease was not found")
        self._remove()

    def _load(self) -> MaintenanceLease | None:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            return MaintenanceLease.from_document(document)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            logger.error("invalid release-maintenance state (%s)", type(exc).__name__)
            raise MaintenanceStateError(
                "release-maintenance state is invalid; operator recovery is required"
            ) from exc

    def _write(self, lease: MaintenanceLease) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{LEASE_FILENAME}.", dir=self._state_dir
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(lease.to_document(), handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            self._fsync_state_dir()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    def _remove(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return
        self._fsync_state_dir()

    def _fsync_state_dir(self) -> None:
        descriptor = os.open(self._state_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
