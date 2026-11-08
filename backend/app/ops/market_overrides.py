"""Operator-managed market calendar overrides: extra holidays and special sessions.

``MARKET_HOLIDAYS`` in ``.env`` needs a reload to change, and nothing in configuration can
express a one-off session such as the Diwali muhurat trading hour, which falls on a
weekend or holiday with its own open and close. This module keeps both in one JSON file
under the state directory so the operator can edit them from the UI while the process
runs.

* A **holiday** closes a day that would otherwise trade.
* A **special session** opens a day — weekend or holiday included — for one window with
  its own open/close, a time from which the broker token is fetched, and how many minutes
  before the open the capture process starts.

Readers go through :func:`active_overrides`, which re-reads the file only when it has
changed (checked at most once per :data:`RECHECK_INTERVAL_S`), so the calendar can consult
it on every tick without disk churn. With no store installed (tests, CLI tools) it returns
an empty snapshot and every calendar behaves exactly as configured.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time as _time
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

OVERRIDES_FILENAME = "market_calendar.json"
RECHECK_INTERVAL_S = 1.0
DEFAULT_CAPTURE_LEAD_MINUTES = 5
# When a special session is saved without a token fetch time, the token is fetched this
# many minutes before its capture start. An explicit time always wins.
DEFAULT_TOKEN_LEAD_MINUTES = 29
MAX_CAPTURE_LEAD_MINUTES = 120
MAX_NOTE_LENGTH = 120
MAX_ENTRIES = 400
# Latest special close: leaves room for the EOD drain tail before midnight.
LATEST_SPECIAL_CLOSE = time(23, 30)
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class OverrideValidationError(ValueError):
    """An override entry is malformed or conflicts with another entry."""


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def _hhmm_from_minutes(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def _lead_minutes(value: object) -> int:
    if value is None:
        return DEFAULT_CAPTURE_LEAD_MINUTES
    if isinstance(value, bool):
        raise OverrideValidationError("capture lead must be a whole number of minutes")
    try:
        lead = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OverrideValidationError(
            f"capture lead must be a whole number of minutes, got {value!r}"
        ) from exc
    if isinstance(value, float) and value != lead:
        raise OverrideValidationError("capture lead must be a whole number of minutes")
    return lead


def _validate_date(value: str) -> str:
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError as exc:
        raise OverrideValidationError(f"invalid date {value!r}; use YYYY-MM-DD") from exc


def _validate_hhmm(value: str, field: str) -> str:
    text = str(value).strip()
    if not _HHMM.match(text):
        raise OverrideValidationError(f"{field} must be HH:MM (24-hour), got {value!r}")
    return text


def _validate_note(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) > MAX_NOTE_LENGTH:
        raise OverrideValidationError(f"note must be at most {MAX_NOTE_LENGTH} characters")
    return text


@dataclass(frozen=True)
class Holiday:
    date: str
    note: str = ""

    @classmethod
    def create(cls, day: str, note: str | None = None) -> Holiday:
        return cls(date=_validate_date(day), note=_validate_note(note))


@dataclass(frozen=True)
class SpecialSession:
    """One extra trading window on one date, applied to every market segment."""

    date: str
    open: str
    close: str
    token_fetch_at: str
    capture_lead_minutes: int = DEFAULT_CAPTURE_LEAD_MINUTES
    note: str = ""

    @classmethod
    def create(
        cls,
        day: str,
        open_at: str,
        close_at: str,
        token_fetch_at: str | None = None,
        capture_lead_minutes: int | None = None,
        note: str | None = None,
    ) -> SpecialSession:
        opened = _validate_hhmm(open_at, "open")
        closed = _validate_hhmm(close_at, "close")
        if _parse_hhmm(closed) <= _parse_hhmm(opened):
            raise OverrideValidationError("close must be after open")
        if _parse_hhmm(closed) > LATEST_SPECIAL_CLOSE:
            raise OverrideValidationError(
                f"close must be at or before {LATEST_SPECIAL_CLOSE:%H:%M} so the "
                "end-of-day sweep can run the same day"
            )
        lead = _lead_minutes(capture_lead_minutes)
        if not 0 <= lead <= MAX_CAPTURE_LEAD_MINUTES:
            raise OverrideValidationError(
                f"capture lead must be between 0 and {MAX_CAPTURE_LEAD_MINUTES} minutes"
            )
        if _minutes(_parse_hhmm(opened)) - lead < 0:
            raise OverrideValidationError("capture lead reaches before midnight")
        capture_start = _minutes(_parse_hhmm(opened)) - lead
        if token_fetch_at is None or not str(token_fetch_at).strip():
            token_at = _hhmm_from_minutes(max(0, capture_start - DEFAULT_TOKEN_LEAD_MINUTES))
        else:
            token_at = _validate_hhmm(token_fetch_at, "token fetch time")
        if _minutes(_parse_hhmm(token_at)) >= capture_start:
            raise OverrideValidationError(
                "token fetch time must be before the capture start "
                f"({_hhmm_from_minutes(capture_start)})"
            )
        return cls(
            date=_validate_date(day),
            open=opened,
            close=closed,
            token_fetch_at=token_at,
            capture_lead_minutes=lead,
            note=_validate_note(note),
        )

    @property
    def open_time(self) -> time:
        return _parse_hhmm(self.open)

    @property
    def close_time(self) -> time:
        return _parse_hhmm(self.close)

    @property
    def token_fetch_time(self) -> time:
        return _parse_hhmm(self.token_fetch_at)

    @property
    def capture_start_time(self) -> time:
        return _parse_hhmm(
            _hhmm_from_minutes(_minutes(self.open_time) - self.capture_lead_minutes)
        )


@dataclass(frozen=True)
class MarketOverrides:
    holidays: tuple[Holiday, ...] = ()
    special_sessions: tuple[SpecialSession, ...] = ()

    def holiday_dates(self) -> frozenset[str]:
        return frozenset(h.date for h in self.holidays)

    def special_for(self, day: str) -> SpecialSession | None:
        for session in self.special_sessions:
            if session.date == day:
                return session
        return None

    def with_holiday(self, holiday: Holiday) -> MarketOverrides:
        if self.special_for(holiday.date) is not None:
            raise OverrideValidationError(
                f"{holiday.date} already has a special session; remove it first"
            )
        others = tuple(h for h in self.holidays if h.date != holiday.date)
        return self._checked(replace(self, holidays=tuple(sorted(
            (*others, holiday), key=lambda h: h.date
        ))))

    def without_holiday(self, day: str) -> MarketOverrides:
        return replace(self, holidays=tuple(h for h in self.holidays if h.date != day))

    def with_special(self, session: SpecialSession) -> MarketOverrides:
        if session.date in self.holiday_dates():
            raise OverrideValidationError(
                f"{session.date} is marked as a holiday; remove it first"
            )
        others = tuple(s for s in self.special_sessions if s.date != session.date)
        return self._checked(replace(self, special_sessions=tuple(sorted(
            (*others, session), key=lambda s: s.date
        ))))

    def without_special(self, day: str) -> MarketOverrides:
        return replace(
            self,
            special_sessions=tuple(s for s in self.special_sessions if s.date != day),
        )

    def _checked(self, candidate: MarketOverrides) -> MarketOverrides:
        if len(candidate.holidays) + len(candidate.special_sessions) > MAX_ENTRIES:
            raise OverrideValidationError(f"at most {MAX_ENTRIES} calendar entries")
        return candidate

    def to_dict(self) -> dict:
        return {
            "holidays": [asdict(h) for h in self.holidays],
            "special_sessions": [asdict(s) for s in self.special_sessions],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> MarketOverrides:
        """Strict parse: any bad entry raises. Used where the input must be all valid."""
        result, errors = cls.parse_lenient(payload)
        if errors:
            raise OverrideValidationError("; ".join(errors))
        return result

    @classmethod
    def parse_lenient(cls, payload: object) -> tuple[MarketOverrides, tuple[str, ...]]:
        """Parse entry by entry, keeping every valid one and reporting the rest.

        One hand-edited mistake must not silently discard a special session that a
        capture day depends on, so a bad entry is skipped and named rather than
        rejecting the whole file.
        """
        if not isinstance(payload, dict):
            return cls(), ("calendar file is not a JSON object",)
        result = cls()
        errors: list[str] = []
        holidays = payload.get("holidays") or []
        specials = payload.get("special_sessions") or []
        if not isinstance(holidays, list):
            errors.append("'holidays' is not a list; all holidays skipped")
            holidays = []
        if not isinstance(specials, list):
            errors.append("'special_sessions' is not a list; all special sessions skipped")
            specials = []
        for item in holidays:
            try:
                result = result.with_holiday(Holiday.create(item["date"], item.get("note")))
            except (ValueError, OverflowError, KeyError, TypeError, AttributeError) as exc:
                errors.append(f"holiday {item!r} skipped: {exc}")
        for item in specials:
            try:
                result = result.with_special(
                    SpecialSession.create(
                        item["date"],
                        item["open"],
                        item["close"],
                        item.get("token_fetch_at"),
                        item.get("capture_lead_minutes"),
                        item.get("note"),
                    )
                )
            except (ValueError, OverflowError, KeyError, TypeError, AttributeError) as exc:
                errors.append(f"special session {item!r} skipped: {exc}")
        return result, tuple(errors)


EMPTY = MarketOverrides()


class MarketOverrideStore:
    """The JSON file behind the overrides, with change-detecting cached reads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._cached = EMPTY
        self._cached_mtime: tuple[int, int] | None = None
        self._checked_at = 0.0
        self._errors: tuple[str, ...] = ()

    @property
    def errors(self) -> tuple[str, ...]:
        """Problems found in the file on its last read (empty when it parsed cleanly)."""
        return self._errors

    def load(self) -> MarketOverrides:
        now = _time.monotonic()
        if now - self._checked_at < RECHECK_INTERVAL_S:
            return self._cached
        with self._lock:
            self._checked_at = now
            try:
                stat = self.path.stat()
                mtime = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                self._cached, self._cached_mtime, self._errors = EMPTY, None, ()
                return self._cached
            except OSError:
                logger.warning("market calendar file is unreadable; keeping last copy")
                return self._cached
            if mtime == self._cached_mtime:
                return self._cached
            # Recorded before parsing so a bad file is logged once, not once a second.
            self._cached_mtime = mtime
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                self._errors = (f"calendar file is not valid JSON: {exc}",)
                logger.error(
                    "market calendar file %s is not valid JSON; keeping the last good copy",
                    self.path,
                )
                return self._cached
            try:
                self._cached, self._errors = MarketOverrides.parse_lenient(payload)
            except Exception as exc:  # noqa: BLE001 - never lose the last good copy
                self._errors = (f"calendar file could not be parsed: {exc}",)
                logger.error("market calendar %s could not be parsed", self.path, exc_info=True)
                return self._cached
            for error in self._errors:
                logger.error("market calendar %s: %s", self.path, error)
            return self._cached

    def save(self, overrides: MarketOverrides) -> MarketOverrides:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._preserve_rejected_file()
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".market_calendar.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(overrides.to_dict(), handle, indent=2)
                    handle.write("\n")
                os.replace(tmp, self.path)
            except Exception:
                Path(tmp).unlink(missing_ok=True)
                raise
            self._cached = overrides
            self._errors = ()
            saved = self.path.stat()
            self._cached_mtime = (saved.st_mtime_ns, saved.st_size)
            self._checked_at = _time.monotonic()
            return overrides

    def _preserve_rejected_file(self) -> None:
        """Keep a copy of a file that had problems before it is overwritten.

        A UI write rebuilds the file from the parsed entries, so anything the parser
        skipped (or a file that was not valid JSON at all) would otherwise be lost.
        """
        if not self._errors or not self.path.exists():
            return
        stamp = _time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.stem}.rejected-{stamp}{self.path.suffix}")
        try:
            shutil.copy2(self.path, backup)
            logger.warning(
                "market calendar had problems; previous file kept as %s", backup.name
            )
        except OSError:
            logger.warning("could not keep a copy of the problem calendar file", exc_info=True)

    def reload(self) -> MarketOverrides:
        self._checked_at = 0.0
        return self.load()

    def cached(self) -> MarketOverrides:
        """The last successfully loaded overrides, without touching the disk."""
        return self._cached


_installed: MarketOverrideStore | None = None


def install(store: MarketOverrideStore | None) -> None:
    """Make ``store`` the process-wide source every :class:`TradingCalendar` consults."""
    global _installed
    _installed = store


def installed_store() -> MarketOverrideStore | None:
    return _installed


def active_overrides() -> MarketOverrides:
    store = _installed
    if store is None:
        return EMPTY
    try:
        return store.load()
    except Exception:  # noqa: BLE001 - the calendar must never fail on the overrides file
        # The last good copy, never EMPTY: an empty answer mid-session reads as "not a
        # trading day" and would stop capture during a special session.
        logger.warning("market calendar overrides unavailable", exc_info=True)
        return store.cached()


def default_store_path(state_dir: Path) -> Path:
    return Path(state_dir) / OVERRIDES_FILENAME


def install_from_settings(settings) -> MarketOverrideStore:
    """Install the store under ``settings.state_dir`` (every entry point calls this)."""
    store = MarketOverrideStore(default_store_path(settings.state_dir))
    install(store)
    return store


def minutes_before(moment: time, minutes: int) -> time:
    shifted = datetime.combine(date(2000, 1, 1), moment) - timedelta(minutes=minutes)
    return shifted.time()


__all__ = [
    "DEFAULT_CAPTURE_LEAD_MINUTES",
    "Holiday",
    "MarketOverrideStore",
    "MarketOverrides",
    "OverrideValidationError",
    "SpecialSession",
    "active_overrides",
    "default_store_path",
    "install",
    "install_from_settings",
    "installed_store",
]
