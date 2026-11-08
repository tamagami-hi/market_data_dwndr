"""Legacy v1 index frames on a wrong strike axis read as missing, never as wrong data."""

from __future__ import annotations

import numpy as np
import pytest

from app.bin_codec import layout, quarantine
from app.bin_codec.layout import RAW_BLOCK_COLUMNS, SCHEMA_VERSION_HEADER_AXIS, RawBlock
from app.bin_codec.reader import IndexBinReader
from app.ops.retention import verify_integrity

STEP = 5_000  # paise


def _axis(n: int, start: int = 2_400_000) -> np.ndarray:
    return np.arange(start, start + n * STEP, STEP, dtype="<i8")


def _write_v1(path, header_strikes: np.ndarray, frames: list[tuple[int, int]]) -> None:
    """A schema-v1 file; ``frames`` are ``(timestamp, width)`` so widths can differ."""
    out = bytearray()
    header = bytearray()
    layout.put_u32(header, layout.TAG_HEADER)
    layout.put_u32(header, SCHEMA_VERSION_HEADER_AXIS)
    layout.put_string(header, "2026-08-26")
    layout.put_string(header, "NIFTY")
    layout.put_string(header, "2026-08-28")
    layout.put_f64(header, 0.065)
    layout.put_vec(header, header_strikes, layout.DT_I64)
    out += layout.frame_bytes(header)
    for seq, (ts, width) in enumerate(frames):
        body = bytearray()
        layout.put_u32(body, layout.TAG_DATA)
        layout.put_u64(body, ts)
        layout.put_u64(body, seq)
        layout.put_i64(body, 2_456_705)
        layout.put_i64(body, 1_234)
        for block in (RawBlock.zeros(width), RawBlock.zeros(width)):
            for col in RAW_BLOCK_COLUMNS:
                layout.put_vec(body, block.columns[col.name], col.dtype)
        out += layout.frame_bytes(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))


@pytest.fixture
def v1_file(tmp_path, monkeypatch):
    path = tmp_path / "INDICES" / "NIFTY" / "2026-08-26.bin"
    # Generation 1 (header writer) at width 98, then a restart: same width but shifted
    # (listed in the manifest), then a restart at width 97 (wrong by construction).
    frames = [(1_000, 98), (2_000, 98), (3_000, 98), (4_000, 98), (5_000, 97), (6_000, 97)]
    _write_v1(path, _axis(98), frames)
    ranges = {("NIFTY", "2026-08-26.bin"): ((3_000, 4_000, "strikes shifted about -1 step(s)"),)}
    monkeypatch.setattr(
        quarantine, "excluded_ranges", lambda p: ranges.get(quarantine._file_key(p), ())
    )
    return path


def test_listed_and_width_mismatched_frames_read_as_missing(v1_file):
    with IndexBinReader(v1_file) as reader:
        assert reader.timestamps == [1_000, 2_000]
        assert reader.excluded_frames == 4
        assert reader.excluded_reasons == {
            "strikes shifted about -1 step(s)": 2,
            "frame width 97 differs from the header's 98 strikes": 2,
        }
        # What remains decodes with its correct header axis.
        assert reader.frame(0).strikes.shape[0] == 98
        assert reader.frame_at(5_500).timestamp_unix_ms == 2_000  # gap, not wrong data


def test_raw_access_still_returns_every_record(v1_file):
    with IndexBinReader(v1_file, include_mislabelled=True) as reader:
        assert len(reader) == 6
        assert reader.excluded_frames == 0


def test_integrity_check_is_structural_and_ignores_the_quarantine(v1_file):
    assert verify_integrity(v1_file) is True


def test_current_format_files_are_untouched(tmp_path):
    from tests.test_capture import _nifty_table
    from tests.test_restart_resume import _write_frames

    path = tmp_path / "INDICES" / "NIFTY" / "2026-11-06.bin"
    _write_frames(path, _nifty_table(), 3)
    with IndexBinReader(path) as reader:
        assert len(reader) == 3 and reader.excluded_frames == 0


def test_the_shipped_manifest_covers_the_affected_days_only():
    summary = quarantine.summary_by_date()
    assert "2026-08-26" in summary and summary["2026-08-26"]["frames"] > 0
    assert "NIFTY" in summary["2026-08-11"]["artifacts"]
    # Days verified clean, and every day after the per-frame strike axis, are absent.
    for clean in ("2026-07-23", "2026-07-24", "2026-07-27", "2026-08-07", "2026-09-01"):
        assert clean not in summary
    for entry in quarantine._manifest()["files"]:
        for r in entry["ranges"]:
            assert r["from_ms"] <= r["to_ms"] and r["frames"] > 0 and r["reason"]


def test_download_history_reports_mislabelled_frames_per_day(tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.capture import create_capture_router

    archive = tmp_path / "archive"
    for day in ("2026-08-26", "2026-07-23"):
        target = archive / "INDICES" / "NIFTY" / f"{day}.bin.zst"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        market_data_path=tmp_path / "live",
        archive_data_path=archive,
        stats_dir=tmp_path / "stats",
        timezone="Asia/Kolkata",
    )
    app.state.session_service = None
    app.include_router(create_capture_router())

    payload = TestClient(app).get("/api/capture/history").json()
    rows = {r["trading_date"]: r for r in payload["sessions"]}

    assert rows["2026-08-26"]["mislabelled_frames"] > 0
    assert "NIFTY" in rows["2026-08-26"]["mislabelled_artifacts"]
    assert rows["2026-07-23"]["mislabelled_frames"] == 0
    assert rows["2026-07-23"]["mislabelled_artifacts"] == []
