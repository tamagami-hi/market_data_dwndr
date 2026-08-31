"""Every index frame carries its own strike axis, and one file holds them all.

This is the replacement for physical ``.pN.bin`` segment rolling. The strike window is
re-derived on every bootstrap (``build_option_chain`` re-centres ATM +/- 50 on the current
spot and clips against the listed strikes), so across a restart it can change **count** or
keep the count and change **which strikes** it covers. Under the old format the header held
the only strike vector, so:

  * a changed count was detectable, and was handled by rolling a new file;
  * a same-count shift was **not** detectable at all -- measured live on 2026-08-31, where
    BANKNIFTY held width 101 all day while the window slid 19 -> 16 -> 17 -> 18 -> 17
    positions inside the listed universe, every frame silently mis-labelled.

Storing the axis in the frame removes both problems: equal counts no longer imply equal
axes, and no frame can be decoded against a foreign axis because it brings its own.

Wire order is ``[calls x15] [strikes x1] [puts x15]`` and all 31 vectors share one
strictly-ascending axis; ``strikes[i]`` names the contract whose CE data is at
``calls.<field>[i]`` and whose PE data is at ``puts.<field>[i]``.
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest

from app.bin_codec import compress, layout
from app.bin_codec.layout import (
    MAX_STRIKES_PER_FRAME,
    RAW_BLOCK_COLUMNS,
    SCHEMA_VERSION_HEADER_AXIS,
    SCHEMA_VERSION_INDEX,
    IndexFrame,
    IndexHeader,
    RawBlock,
)
from app.bin_codec.reader import IndexBinReader
from app.bin_codec.scan import scan_frames
from app.bin_codec.writer import (
    IndexBinWriter,
    SchemaVersionMismatch,
    encode_index_frame,
    resolve_index_segment_path,
)
from app.chain.filter import option_chain_filter

STEP = 5_000  # 50 rupees in paise


def _header() -> IndexHeader:
    return IndexHeader("2026-09-01", "NIFTY", "2026-09-01", 0.052603)


def _axis(n: int, first: int = 2_200_000) -> np.ndarray:
    """An ascending ``n``-position axis starting at ``first`` paise."""
    return np.arange(first, first + n * STEP, STEP, dtype=np.int64)


def _frame(ts: int, seq: int, strikes: np.ndarray, *, mark: int = 0) -> IndexFrame:
    """A frame whose per-strike values encode their own strike, so mis-mapping shows up.

    ``ltp`` at position ``i`` is ``strikes[i] + mark``, which means a frame decoded against
    the wrong axis produces values that do not correspond to the strikes claimed for it.
    """
    n = int(strikes.shape[0])
    calls, puts = RawBlock.zeros(n), RawBlock.zeros(n)
    calls.columns["ltp"][:] = strikes + mark
    puts.columns["ltp"][:] = strikes + mark + 1
    calls.columns["oi"][:] = np.arange(n, dtype=np.uint64)
    puts.columns["oi"][:] = np.arange(n, dtype=np.uint64)
    return IndexFrame(ts, seq, 2_456_705, 1_234, calls, strikes.copy(), puts)


def _generation(path, strikes: np.ndarray, timestamps, *, mark: int = 0) -> None:
    """One capture generation: open, write the header if empty, append, close."""
    writer = IndexBinWriter(path)
    writer.open()
    writer.bind_header(_header())
    writer.flush_header()
    for i, ts in enumerate(timestamps):
        writer.append_frame(_frame(ts, i, strikes, mark=mark))
    writer.close()


# --------------------------------------------------------------------------- #
# 1. Variable width, one physical file
# --------------------------------------------------------------------------- #


def test_three_generations_with_three_widths_stay_in_one_file(tmp_path):
    """94 -> 92 -> 101 strikes across two restarts, all in ``<date>.bin``."""
    path = tmp_path / "2026-09-01.bin"
    plan = [(94, 1_000), (92, 5_000), (101, 9_000)]

    for n, base_ts in plan:
        target = resolve_index_segment_path(path)
        assert target == path, "an axis change must never roll a segment"
        _generation(target, _axis(n), [base_ts + i * 1_000 for i in range(3)], mark=n)

    assert sorted(p.name for p in tmp_path.glob("*.bin")) == ["2026-09-01.bin"]
    assert scan_frames(path).frames == 9

    with IndexBinReader(path) as reader:
        widths = [reader.frame(i).axis_length() for i in range(len(reader))]
        assert widths == [94, 94, 94, 92, 92, 92, 101, 101, 101]
        # Each frame's values must match the axis it carries.
        for i in range(len(reader)):
            frame = reader.frame(i)
            n = frame.axis_length()
            np.testing.assert_array_equal(frame.calls.columns["ltp"], frame.strikes + n)
            np.testing.assert_array_equal(frame.strikes, _axis(n))


def test_a_single_writer_can_emit_mixed_widths(tmp_path):
    """Width comes from the frame, not from the file or a bound axis."""
    path = tmp_path / "2026-09-01.bin"
    writer = IndexBinWriter(path)
    writer.open()
    writer.write_header(_header())
    for i, n in enumerate((94, 101, 85, 92)):
        writer.append_frame(_frame(1_000 + i * 1_000, i, _axis(n), mark=n))
    writer.close()

    with IndexBinReader(path) as reader:
        assert [reader.frame(i).axis_length() for i in range(4)] == [94, 101, 85, 92]


# --------------------------------------------------------------------------- #
# 2. Same count, different strikes -- the case the old format could not see
# --------------------------------------------------------------------------- #


def test_same_count_different_strikes_are_distinguishable(tmp_path):
    """95 strikes axis A, restart, 95 strikes axis B: both preserved, no roll."""
    path = tmp_path / "2026-09-01.bin"
    axis_a = _axis(95, first=2_200_000)
    axis_b = _axis(95, first=2_200_000 + 3 * STEP)  # same width, slid 3 positions
    assert axis_a.shape == axis_b.shape
    assert not np.array_equal(axis_a, axis_b)

    _generation(path, axis_a, [1_000, 2_000], mark=1)
    assert resolve_index_segment_path(path) == path, "same count, no roll"
    _generation(path, axis_b, [3_000, 4_000], mark=2)

    assert sorted(p.name for p in tmp_path.glob("*.bin")) == ["2026-09-01.bin"]

    with IndexBinReader(path) as reader:
        assert len(reader) == 4
        for i in (0, 1):
            np.testing.assert_array_equal(reader.frame(i).strikes, axis_a)
            np.testing.assert_array_equal(
                reader.frame(i).calls.columns["ltp"], axis_a + 1
            )
        for i in (2, 3):
            np.testing.assert_array_equal(reader.frame(i).strikes, axis_b)
            np.testing.assert_array_equal(
                reader.frame(i).calls.columns["ltp"], axis_b + 2
            )


def test_an_oscillating_axis_returns_to_its_original_strikes(tmp_path):
    """A -> B -> A: the third generation's frames carry axis A again, in the same file."""
    path = tmp_path / "2026-09-01.bin"
    axis_a = _axis(95)
    axis_b = _axis(95, first=2_200_000 + 2 * STEP)

    _generation(path, axis_a, [1_000], mark=1)
    _generation(path, axis_b, [2_000], mark=2)
    _generation(path, axis_a, [3_000], mark=3)

    with IndexBinReader(path) as reader:
        np.testing.assert_array_equal(reader.frame(0).strikes, axis_a)
        np.testing.assert_array_equal(reader.frame(1).strikes, axis_b)
        np.testing.assert_array_equal(reader.frame(2).strikes, axis_a)
    assert sorted(p.name for p in tmp_path.glob("*.bin")) == ["2026-09-01.bin"]


# --------------------------------------------------------------------------- #
# 3. Random access across an axis transition
# --------------------------------------------------------------------------- #


def test_random_access_resolves_the_axis_on_both_sides_of_a_transition(tmp_path):
    path = tmp_path / "2026-09-01.bin"
    axis_a, axis_b = _axis(94), _axis(101, first=2_150_000)
    _generation(path, axis_a, [10_000, 11_000], mark=7)
    _generation(path, axis_b, [12_000, 13_000], mark=8)

    with IndexBinReader(path) as reader:
        before = reader.frame_at(11_000)
        after = reader.frame_at(12_000)
        assert before.axis_length() == 94
        assert after.axis_length() == 101
        np.testing.assert_array_equal(before.strikes, axis_a)
        np.testing.assert_array_equal(after.strikes, axis_b)

        spanning = reader.frames_in_range(10_000, 13_000)
        assert [f.axis_length() for f in spanning] == [94, 94, 101, 101]
        # A nearest-timestamp lookup must never borrow a neighbour's axis.
        for frame in spanning:
            np.testing.assert_array_equal(
                frame.calls.columns["ltp"], frame.strikes + (7 if frame.axis_length() == 94 else 8)
            )


# --------------------------------------------------------------------------- #
# 4. The frame is rejected, not written, when the axis contract is broken
# --------------------------------------------------------------------------- #


def test_a_wing_that_does_not_match_the_strike_vector_is_rejected():
    strikes = _axis(5)
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(5), strikes, RawBlock.zeros(4)
    )
    with pytest.raises(ValueError, match="length != 5"):
        encode_index_frame(frame)


def test_a_strike_vector_that_does_not_match_the_wings_is_rejected():
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(5), _axis(4), RawBlock.zeros(5)
    )
    with pytest.raises(ValueError, match="length != 4"):
        encode_index_frame(frame)


def test_an_unsorted_axis_is_rejected():
    strikes = _axis(5)[::-1].copy()  # descending
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(5), strikes, RawBlock.zeros(5)
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        encode_index_frame(frame)


def test_a_duplicated_strike_is_rejected():
    """Two positions claiming one strike makes the axis ambiguous."""
    strikes = np.array([100, 200, 200, 300, 400], dtype=np.int64)
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(5), strikes, RawBlock.zeros(5)
    )
    with pytest.raises(ValueError, match="strictly ascending"):
        encode_index_frame(frame)


def test_an_axis_above_the_maximum_is_rejected():
    n = MAX_STRIKES_PER_FRAME + 1
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(n), _axis(n), RawBlock.zeros(n)
    )
    with pytest.raises(ValueError, match=f"above the {MAX_STRIKES_PER_FRAME} maximum"):
        encode_index_frame(frame)


def test_an_empty_axis_is_rejected():
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(0), _axis(0), RawBlock.zeros(0)
    )
    with pytest.raises(ValueError, match="must carry its axis"):
        encode_index_frame(frame)


def test_a_float_axis_is_rejected():
    """Strikes stay integer paise; a float axis would lose exactness."""
    frame = IndexFrame(
        1_000, 0, 0, 0, RawBlock.zeros(3), _axis(3).astype(np.float64), RawBlock.zeros(3)
    )
    with pytest.raises(ValueError, match="integer paise"):
        encode_index_frame(frame)


def test_a_rejected_frame_leaves_the_file_untouched(tmp_path):
    """Validation happens before any bytes are appended."""
    path = tmp_path / "2026-09-01.bin"
    _generation(path, _axis(94), [1_000])
    size_before = path.stat().st_size

    writer = IndexBinWriter(path)
    writer.open()
    writer.bind_header(_header())
    bad = IndexFrame(2_000, 1, 0, 0, RawBlock.zeros(5), _axis(4), RawBlock.zeros(5))
    with pytest.raises(ValueError):
        writer.append_frame(bad)
    writer.close()

    assert path.stat().st_size == size_before
    assert scan_frames(path).frames == 1


def test_the_frame_maximum_tracks_the_selection_default():
    """``MAX_STRIKES_PER_FRAME`` must stay in step with ATM +/- N selection."""
    per_side = inspect.signature(option_chain_filter).parameters["strikes_per_side"].default
    assert MAX_STRIKES_PER_FRAME == 2 * per_side + 1


# --------------------------------------------------------------------------- #
# 5. Legacy v1 files are readable and are never written to
# --------------------------------------------------------------------------- #


def _write_v1_file(path, strikes: np.ndarray, timestamps) -> bytes:
    """Hand-build a schema-v1 index file: axis in the header, no axis in the frames."""
    n = int(strikes.shape[0])
    out = bytearray()

    header = bytearray()
    layout.put_u32(header, layout.TAG_HEADER)
    layout.put_u32(header, SCHEMA_VERSION_HEADER_AXIS)
    layout.put_string(header, "2026-08-27")
    layout.put_string(header, "NIFTY")
    layout.put_string(header, "2026-09-01")
    layout.put_f64(header, 0.052603)
    layout.put_vec(header, strikes, layout.DT_I64)
    out += layout.frame_bytes(header)

    for seq, ts in enumerate(timestamps):
        calls, puts = RawBlock.zeros(n), RawBlock.zeros(n)
        calls.columns["ltp"][:] = strikes
        puts.columns["ltp"][:] = strikes + 1
        body = bytearray()
        layout.put_u32(body, layout.TAG_DATA)
        layout.put_u64(body, ts)
        layout.put_u64(body, seq)
        layout.put_i64(body, 2_456_705)
        layout.put_i64(body, 1_234)
        for block in (calls, puts):  # v1 order: calls then puts, no strike vector
            for col in RAW_BLOCK_COLUMNS:
                layout.put_vec(body, block.columns[col.name], col.dtype)
        out += layout.frame_bytes(body)

    path.write_bytes(bytes(out))
    return bytes(out)


def test_a_legacy_v1_file_still_reads_with_its_header_axis(tmp_path):
    path = tmp_path / "2026-08-27.bin"
    strikes = _axis(97)
    _write_v1_file(path, strikes, [1_000, 2_000, 3_000])

    with IndexBinReader(path) as reader:
        assert reader.schema_version == SCHEMA_VERSION_HEADER_AXIS
        assert reader.header().schema_version == SCHEMA_VERSION_HEADER_AXIS
        assert len(reader) == 3
        for i in range(3):
            frame = reader.frame(i)
            # The header axis is attached to every frame, so callers never branch.
            np.testing.assert_array_equal(frame.strikes, strikes)
            np.testing.assert_array_equal(frame.calls.columns["ltp"], strikes)
    assert scan_frames(path).frames == 3


def test_a_legacy_v1_file_is_never_appended_to_and_is_left_byte_identical(tmp_path):
    """v1 and v2 frames decode differently, so they must not share a file."""
    path = tmp_path / "2026-08-27.bin"
    original = _write_v1_file(path, _axis(97), [1_000, 2_000])

    target = resolve_index_segment_path(path)
    assert target.name == "2026-08-27.p2.bin", "a schema change steps aside, once"

    _generation(target, _axis(94), [3_000, 4_000])

    assert path.read_bytes() == original, "the captured v1 file must not be modified"
    with IndexBinReader(target) as reader:
        assert reader.schema_version == SCHEMA_VERSION_INDEX
        assert reader.frame(0).axis_length() == 94


def test_appending_v2_frames_onto_a_v1_file_raises(tmp_path):
    path = tmp_path / "2026-08-27.bin"
    _write_v1_file(path, _axis(97), [1_000])

    writer = IndexBinWriter(path)
    writer.open()
    try:
        with pytest.raises(SchemaVersionMismatch) as excinfo:
            writer.bind_header(_header())
    finally:
        writer.close()
    assert excinfo.value.on_disk == SCHEMA_VERSION_HEADER_AXIS
    assert excinfo.value.current == SCHEMA_VERSION_INDEX


# --------------------------------------------------------------------------- #
# 6. EOD still produces one archive per artifact-day
# --------------------------------------------------------------------------- #


def test_a_day_with_axis_changes_compresses_to_one_archive(tmp_path):
    path = tmp_path / "2026-09-01.bin"
    _generation(path, _axis(94), [1_000, 2_000], mark=1)
    _generation(path, _axis(92), [3_000, 4_000], mark=2)
    _generation(path, _axis(101), [5_000, 6_000], mark=3)

    zst = compress.compress_file(path, remove_src=True)
    assert zst.name == "2026-09-01.bin.zst"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2026-09-01.bin.zst"]

    with IndexBinReader(zst) as reader:
        assert [reader.frame(i).axis_length() for i in range(len(reader))] == [
            94, 94, 92, 92, 101, 101,
        ]
        for i in range(len(reader)):
            frame = reader.frame(i)
            np.testing.assert_array_equal(frame.strikes, _axis(frame.axis_length()))
