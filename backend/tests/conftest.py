"""Shared fixtures / builders for BIN codec tests."""

from __future__ import annotations

import numpy as np

from app.bin_codec.layout import (
    DEPTH_LEVEL_COLUMNS,
    DEPTH_LEVELS,
    INSTR_SCALAR_COLUMNS,
    RAW_BLOCK_COLUMNS,
    FutureRef,
    IndexFrame,
    IndexHeader,
    InstrColumns,
    RawBlock,
    StockFrame,
    StockHeader,
    StockRef,
)

_U64_MAX = np.uint64(2**64 - 1)


def _rand_column(rng: np.random.Generator, dtype: np.dtype, n: int) -> np.ndarray:
    """Random values that exercise the full range of the dtype, incl. edges."""
    if dtype == np.dtype("<i8"):
        vals = rng.integers(-(10**9), 10**9, size=n, dtype=np.int64).astype(dtype)
        if n:
            vals[0] = -2_456_705  # a realistic negative-ish paise edge
    elif dtype == np.dtype("<u8"):
        vals = rng.integers(0, 2**63 - 1, size=n, dtype=np.uint64).astype(dtype)
        if n:
            vals[0] = _U64_MAX  # max u64 edge
    elif dtype == np.dtype("<u4"):
        vals = rng.integers(0, 2**32, size=n, dtype=np.uint32).astype(dtype)
    else:  # pragma: no cover - defensive
        raise AssertionError(f"unexpected dtype {dtype}")
    return vals


def make_raw_block(rng: np.random.Generator, n: int) -> RawBlock:
    return RawBlock({c.name: _rand_column(rng, c.dtype, n) for c in RAW_BLOCK_COLUMNS})


def make_instr_columns(rng: np.random.Generator, n: int) -> InstrColumns:
    scalars = {c.name: _rand_column(rng, c.dtype, n) for c in INSTR_SCALAR_COLUMNS}
    depth = [
        {c.name: _rand_column(rng, c.dtype, n) for c in DEPTH_LEVEL_COLUMNS}
        for _ in range(DEPTH_LEVELS)
    ]
    return InstrColumns(scalars, depth)


def make_index_header(n_strikes: int = 5) -> IndexHeader:
    strikes = np.array(
        [2_400_000 + i * 5_000 for i in range(n_strikes)], dtype="<i8"
    )
    return IndexHeader(
        trading_date="2026-07-21",
        underlying="NIFTY",
        expiry_date="2026-07-24",
        risk_free_rate=0.0691,
        strikes=strikes,
    )


def make_index_frame(rng: np.random.Generator, n: int, ts: int, seq: int) -> IndexFrame:
    return IndexFrame(
        timestamp_unix_ms=ts,
        sequence=seq,
        spot_price=2_456_705,
        vix=1_234,
        calls=make_raw_block(rng, n),
        puts=make_raw_block(rng, n),
    )


def make_stock_header() -> StockHeader:
    return StockHeader(
        trading_date="2026-07-21",
        risk_free_rate=0.0691,
        stocks=[
            StockRef(
                tradingsymbol="RELIANCE",
                name="RELIANCE",
                spot_token=738561,
                lot_size=250,
                futures=[
                    FutureRef(token=1001, expiry="2026-07-31", lot_size=250),
                    FutureRef(token=1002, expiry="2026-08-28", lot_size=250),
                    FutureRef(token=1003, expiry="2026-09-25", lot_size=250),
                ],
            ),
            # A stock with fewer than 3 futures (missing-slot case).
            StockRef(
                tradingsymbol="M&M",
                name="M&M",
                spot_token=519937,
                lot_size=700,
                futures=[FutureRef(token=2001, expiry="2026-07-31", lot_size=700)],
            ),
        ],
    )


def make_stock_frame(rng: np.random.Generator, n: int, ts: int, seq: int) -> StockFrame:
    return StockFrame(
        timestamp_unix_ms=ts,
        sequence=seq,
        spot=make_instr_columns(rng, n),
        fut_current=make_instr_columns(rng, n),
        fut_mid=make_instr_columns(rng, n),
        fut_far=make_instr_columns(rng, n),
    )
