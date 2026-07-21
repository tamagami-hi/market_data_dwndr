"""Historical job management: checkpoints, resume, concurrency, progress.

Multiple contracts download concurrently (bounded by a semaphore, sharing one rate
limiter in the client). Per-contract checkpoints under ``_state/`` record completed
windows + rows written so a job **resumes** after interruption without re-fetching
finished windows (docs/40-historical/historical-data.md). Progress is streamed via a
callback (wired to the ``historical-jobs`` WS topic).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.historical.client import Candle, HistoricalClient
from app.ws import protocol

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_ERROR = "error"


def window_key(start: datetime, end: datetime) -> str:
    return f"{start.isoformat()}..{end.isoformat()}"


@dataclass
class ContractCheckpoint:
    token: int
    status: str = STATUS_PENDING
    completed_windows: list[str] = field(default_factory=list)
    rows_written: int = 0
    last_completed_timestamp_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ContractCheckpoint:
        return cls(
            token=int(data["token"]),
            status=data.get("status", STATUS_PENDING),
            completed_windows=list(data.get("completed_windows", [])),
            rows_written=int(data.get("rows_written", 0)),
            last_completed_timestamp_ms=data.get("last_completed_timestamp_ms"),
        )


class JobStore:
    """Persists job request + per-contract checkpoints under ``_state/``."""

    def __init__(self, state_dir: str | os.PathLike[str]) -> None:
        self.state_dir = Path(state_dir)

    def _request_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}_request.json"

    def _checkpoint_path(self, job_id: str, token: int) -> Path:
        return self.state_dir / f"{job_id}_{token}.json"

    def save_request(self, job_id: str, request: dict) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._request_path(job_id)
        path.write_text(json.dumps(request, indent=2, default=str), encoding="utf-8")
        return path

    def load_request(self, job_id: str) -> dict | None:
        path = self._request_path(job_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def save_checkpoint(self, job_id: str, cp: ContractCheckpoint) -> Path:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._checkpoint_path(job_id, cp.token)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cp.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def load_checkpoint(self, job_id: str, token: int) -> ContractCheckpoint | None:
        path = self._checkpoint_path(job_id, token)
        if not path.exists():
            return None
        return ContractCheckpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_checkpoints(self, job_id: str) -> list[ContractCheckpoint]:
        out = []
        for path in sorted(self.state_dir.glob(f"{job_id}_*.json")):
            if path.name.endswith("_request.json"):
                continue
            out.append(ContractCheckpoint.from_dict(json.loads(path.read_text("utf-8"))))
        return out

    def list_jobs(self) -> list[str]:
        return sorted(
            p.name[: -len("_request.json")] for p in self.state_dir.glob("*_request.json")
        )


class HistoricalJob:
    """Runs one download job: fetch windows per token, checkpoint, and resume."""

    def __init__(
        self,
        job_id: str,
        client: HistoricalClient,
        store: JobStore,
        *,
        interval: str,
        oi: bool = True,
        max_concurrency: int = 4,
        progress_cb: Callable[[dict], None] | None = None,
    ) -> None:
        self.job_id = job_id
        self.client = client
        self.store = store
        self.interval = interval
        self.oi = oi
        self.max_concurrency = max_concurrency
        self.progress_cb = progress_cb
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def _emit(self, cp: ContractCheckpoint, windows_total: int) -> None:
        if self.progress_cb is None:
            return
        self.progress_cb(
            protocol.historical_job_update(
                {
                    "job_id": self.job_id,
                    "token": cp.token,
                    "status": cp.status,
                    "rows_written": cp.rows_written,
                    "windows_done": len(cp.completed_windows),
                    "windows_total": windows_total,
                    "last_completed_timestamp_ms": cp.last_completed_timestamp_ms,
                }
            )
        )

    async def run(
        self,
        tokens: list[int],
        windows: list[tuple[datetime, datetime]],
    ) -> dict[int, list[Candle]]:
        """Download all tokens over all windows, resuming from any checkpoints."""
        sem = asyncio.Semaphore(self.max_concurrency)
        results: dict[int, list[Candle]] = {}

        async def do_token(token: int) -> None:
            cp = self.store.load_checkpoint(self.job_id, token) or ContractCheckpoint(token=token)
            cp.status = STATUS_RUNNING
            self.store.save_checkpoint(self.job_id, cp)
            collected: list[Candle] = []
            for start, end in windows:
                if self._cancelled.is_set():
                    cp.status = STATUS_CANCELLED
                    self.store.save_checkpoint(self.job_id, cp)
                    self._emit(cp, len(windows))
                    results[token] = collected
                    return
                key = window_key(start, end)
                if key in cp.completed_windows:
                    continue  # resume: already fetched
                async with sem:
                    candles = await self.client.fetch_window(
                        token, self.interval, start, end, self.oi
                    )
                collected.extend(candles)
                cp.completed_windows.append(key)
                cp.rows_written += len(candles)
                if candles:
                    cp.last_completed_timestamp_ms = max(c.timestamp_unix_ms for c in candles)
                self.store.save_checkpoint(self.job_id, cp)
                self._emit(cp, len(windows))
            cp.status = STATUS_DONE
            self.store.save_checkpoint(self.job_id, cp)
            self._emit(cp, len(windows))
            results[token] = collected

        await asyncio.gather(*(do_token(t) for t in tokens))
        return results
