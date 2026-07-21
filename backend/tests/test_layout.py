"""Unit tests for the BIN primitive codec + column specs (layout.py)."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from app.bin_codec import layout
from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    DEPTH_LEVELS,
    INSTR_SCALAR_COLUMNS,
    RAW_BLOCK_COLUMNS,
    Cursor,
    InstrColumns,
    RawBlock,
)


def test_scalar_primitives_round_trip():
    out = bytearray()
    layout.put_u32(out, 0)
    layout.put_u32(out, 4_294_967_295)  # max u32
    layout.put_u64(out, 18_446_744_073_709_551_615)  # max u64
    layout.put_i64(out, -2_456_705)  # negative paise
    layout.put_f64(out, 0.0691)

    cur = Cursor(bytes(out))
    assert cur.u32() == 0
    assert cur.u32() == 4_294_967_295
    assert cur.u64() == 18_446_744_073_709_551_615
    assert cur.i64() == -2_456_705
    assert cur.f64() == pytest.approx(0.0691)
    assert cur.pos == len(out)


def test_string_round_trip_and_layout():
    out = bytearray()
    layout.put_string(out, "M&M")  # non-ASCII-safe symbol style
    # Wire layout: u64 length prefix then UTF-8 bytes.
    assert struct.unpack_from("<Q", out, 0)[0] == 3
    assert bytes(out[8:11]) == b"M&M"
    assert Cursor(bytes(out)).string() == "M&M"


def test_vec_round_trip_preserves_dtype_and_values():
    arr = np.array([1, -2, 2_456_705, -9_223_372_036_854_775_808], dtype=layout.DT_I64)
    out = bytearray()
    layout.put_vec(out, arr, layout.DT_I64)
    # length prefix
    assert struct.unpack_from("<Q", out, 0)[0] == 4
    decoded = Cursor(bytes(out)).vec(layout.DT_I64)
    assert decoded.dtype == layout.DT_I64
    np.testing.assert_array_equal(decoded, arr)


def test_vec_is_little_endian_on_wire():
    out = bytearray()
    layout.put_vec(out, np.array([1], dtype=layout.DT_U32), layout.DT_U32)
    # 8-byte count then 4 LE bytes for the value 1.
    assert bytes(out) == struct.pack("<Q", 1) + struct.pack("<I", 1)


def test_frame_bytes_length_prefix():
    payload = b"hello"
    framed = layout.frame_bytes(payload)
    assert struct.unpack_from("<I", framed, 0)[0] == 5
    assert framed[4:] == payload


def test_column_specs_match_spec_counts():
    assert len(RAW_BLOCK_COLUMNS) == 15
    assert len(INSTR_SCALAR_COLUMNS) == 11
    assert len(DEPTH_LEVEL_COLUMNS) == 6
    assert DEPTH_LEVELS == 5


def test_raw_block_zeros_and_validate():
    block = RawBlock.zeros(101)
    block.validate(101)
    assert block.length() == 101
    with pytest.raises(ValueError):
        RawBlock.zeros(101).columns.pop("ltp") or RawBlock({"ltp": np.zeros(1)}).validate()


def test_instr_columns_zeros_and_validate():
    instr = InstrColumns.zeros(200)
    instr.validate(200)
    assert len(instr.depth) == DEPTH_LEVELS
    assert instr.length() == 200
