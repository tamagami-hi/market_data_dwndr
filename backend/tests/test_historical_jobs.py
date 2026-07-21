"""Tests for historical job checkpoints, resume, cancel, and progress."""

from __future__ import annotations

from datetime import datetime

from app.historical.client import Candle, HistoricalClient
from app.historical.jobs import (
    STATUS_DONE,
    ContractCheckpoint,
    HistoricalJob,
    JobStore,
    window_key,
)
from app.historical.limiter import TokenBucket


def _windows():
    return [
        (datetime(2026, 1, 1), datetime(2026, 1, 2)),
        (datetime(2026, 1, 3), datetime(2026, 1, 4)),
        (datetime(2026, 1, 5), datetime(2026, 1, 6)),
    ]


def _client(fetch_log):
    async def fetcher(token, interval, frm, to, oi):
        fetch_log.append((token, frm, to))
        return [[f"{frm}", 1, 2, 0.5, 1.5, 10, 5]]  # one candle per window

    return HistoricalClient(fetcher, TokenBucket(1000.0, burst=100))


# --- store round-trip --------------------------------------------------------


def test_job_store_request_and_checkpoint(tmp_path):
    store = JobStore(tmp_path)
    store.save_request("job1", {"underlying": "NIFTY", "interval": "day"})
    assert store.load_request("job1")["underlying"] == "NIFTY"
    assert store.list_jobs() == ["job1"]

    cp = ContractCheckpoint(token=111, rows_written=5, completed_windows=["a..b"])
    store.save_checkpoint("job1", cp)
    loaded = store.load_checkpoint("job1", 111)
    assert loaded == cp
    assert [c.token for c in store.list_checkpoints("job1")] == [111]


# --- run + resume ------------------------------------------------------------


async def test_job_downloads_all_windows(tmp_path):
    fetch_log: list = []
    progress: list = []
    job = HistoricalJob(
        "jobA", _client(fetch_log), JobStore(tmp_path), interval="day",
        progress_cb=progress.append,
    )
    results = await job.run([111], _windows())
    assert len(fetch_log) == 3  # three windows fetched
    assert len(results[111]) == 3  # one candle each
    cp = JobStore(tmp_path).load_checkpoint("jobA", 111)
    assert cp.status == STATUS_DONE
    assert cp.rows_written == 3
    assert len(cp.completed_windows) == 3
    assert progress  # progress emitted


async def test_job_resumes_skipping_completed_windows(tmp_path):
    store = JobStore(tmp_path)
    windows = _windows()
    # Pre-seed a checkpoint marking the first window done.
    seeded = ContractCheckpoint(
        token=222,
        completed_windows=[window_key(*windows[0])],
        rows_written=1,
    )
    store.save_checkpoint("jobB", seeded)

    fetch_log: list = []
    job = HistoricalJob("jobB", _client(fetch_log), store, interval="day")
    results = await job.run([222], windows)

    # Only the two remaining windows were fetched (no duplicate of window 0).
    assert len(fetch_log) == 2
    cp = store.load_checkpoint("jobB", 222)
    assert len(cp.completed_windows) == 3  # all now complete
    assert cp.rows_written == 3  # 1 seeded + 2 new
    assert len(results[222]) == 2  # only newly fetched candles returned


async def test_job_cancel_stops_early(tmp_path):
    fetch_log: list = []
    job = HistoricalJob("jobC", _client(fetch_log), JobStore(tmp_path), interval="day")
    job.cancel()  # cancel before running
    results = await job.run([333], _windows())
    assert fetch_log == []  # nothing fetched
    cp = JobStore(tmp_path).load_checkpoint("jobC", 333)
    assert cp.status == "cancelled"
    assert results[333] == []


def test_candle_type_used():
    # sanity: the fetcher path produces Candle objects via the client parser
    c = Candle(1, 1.0, 2.0, 0.5, 1.5, 10, 5)
    assert c.close == 1.5
