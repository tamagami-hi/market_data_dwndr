"""Tests for the persistent statistics store."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ops import stats_store


@dataclass
class _FakeResult:
    compressed: list
    total_raw_bytes: int
    total_zst_bytes: int
    elapsed_ms: int
    file_times_ms: list = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.total_raw_bytes / self.total_zst_bytes if self.total_zst_bytes else 0.0

    @property
    def avg_file_ms(self) -> float:
        return sum(self.file_times_ms) / len(self.file_times_ms) if self.file_times_ms else 0.0

    @property
    def throughput_mbps(self) -> float:
        return (self.total_raw_bytes / 1e6) / (self.elapsed_ms / 1000.0) if self.elapsed_ms else 0.0


def _result(raw, zst, elapsed_ms, files=2, times=(100.0, 200.0)):
    return _FakeResult(
        compressed=[f"f{i}" for i in range(files)],
        total_raw_bytes=raw,
        total_zst_bytes=zst,
        elapsed_ms=elapsed_ms,
        file_times_ms=list(times),
    )


def test_record_and_load_compression_history(tmp_path):
    stats_store.record_compression(
        tmp_path, _result(1000, 200, 400), trading_date="2026-07-21", threads=6
    )
    stats_store.record_compression(
        tmp_path, _result(2000, 500, 800), trading_date="2026-07-22", threads=6
    )
    history = stats_store.load_compression_history(tmp_path)
    assert len(history) == 2
    assert history[0]["trading_date"] == "2026-07-21"
    assert history[0]["ratio"] == 5.0  # 1000 / 200
    assert history[1]["files"] == 2
    assert history[1]["threads"] == 6
    assert history[1]["avg_file_ms"] == 150.0  # mean(100,200)


def test_compression_averages(tmp_path):
    assert stats_store.compression_averages(tmp_path)["samples"] == 0
    stats_store.record_compression(tmp_path, _result(1000, 200, 400), trading_date="2026-07-21")
    stats_store.record_compression(tmp_path, _result(3000, 600, 600), trading_date="2026-07-22")
    avgs = stats_store.compression_averages(tmp_path)
    assert avgs["samples"] == 2
    assert avgs["avg_ratio"] == 5.0  # both are 5.0
    assert avgs["avg_total_elapsed_ms"] == 500.0  # mean(400, 600)
    assert avgs["last"]["trading_date"] == "2026-07-22"


def test_load_compression_history_tolerates_corrupt_lines(tmp_path):
    path = stats_store.compression_history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"trading_date": "2026-07-21", "ratio": 4.0}\nNOT JSON\n\n')
    history = stats_store.load_compression_history(tmp_path)
    assert len(history) == 1
    assert history[0]["ratio"] == 4.0


def test_capture_snapshot_round_trip(tmp_path):
    assert stats_store.load_capture_snapshot(tmp_path, "2026-07-21") is None
    payload = {
        "per_underlying": [{"underlying": "NIFTY", "frames_written": 5}],
        "global": {"fps": 1.0},
    }
    stats_store.write_capture_snapshot(tmp_path, "2026-07-21", payload)
    loaded = stats_store.load_capture_snapshot(tmp_path, "2026-07-21")
    assert loaded == payload


def test_writes_are_atomic_no_temp_left(tmp_path):
    stats_store.record_compression(tmp_path, _result(1000, 200, 400), trading_date="2026-07-21")
    stats_store.write_capture_snapshot(tmp_path, "2026-07-21", {"global": {}})
    leftovers = list(stats_store.stats_dir(tmp_path).glob(".*tmp"))
    assert leftovers == []


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(stats_store, "MAX_COMPRESSION_HISTORY", 3)
    for i in range(5):
        stats_store.record_compression(
            tmp_path, _result(1000, 200, 400), trading_date=f"2026-07-{20 + i:02d}"
        )
    history = stats_store.load_compression_history(tmp_path)
    assert len(history) == 3
    assert history[0]["trading_date"] == "2026-07-22"  # oldest two dropped
    assert history[-1]["trading_date"] == "2026-07-24"
