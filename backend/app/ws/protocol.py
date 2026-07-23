"""Backend -> frontend message protocol.

A tagged-JSON envelope ``{ "type": ..., "payload": ... }`` identical in shape to
algo_engine, so the reused `wsTopicConnection.ts` / `useMarketStore.ts` work with
minimal edits (docs/50-frontend/websocket-protocol.md).

Broadcast values are for **display**: prices are converted paise -> rupees here and are
independent of the integer-native on-disk format. Greeks/IV are never sent (computed on
read if displayed).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.bin_codec.layout import IndexFrame, RawBlock

# --- message type tags -------------------------------------------------------

TYPE_MARKET_HEADER = "MarketHeader"
TYPE_OPTION_GRID = "OptionGrid"
TYPE_OPTION_GRID_DELTA = "OptionGridDelta"
TYPE_STOCK_BOARD = "StockBoard"
TYPE_CAPTURE_STATUS = "CaptureStatus"
TYPE_HEARTBEAT = "Heartbeat"
TYPE_SESSION_STATUS = "SessionStatus"
TYPE_LOG = "Log"
TYPE_HISTORICAL_JOB_UPDATE = "HistoricalJobUpdate"
TYPE_COMPRESSION_PROGRESS = "CompressionProgress"

# Full keyframe cadence (algo_engine parity): a full OptionGrid every N frames.
KEYFRAME_INTERVAL = 30

# GridBlock fields sent to the UI (subset of RawBlock; prices in rupees).
GRID_FIELDS: tuple[tuple[str, bool], ...] = (
    ("ltp", True),
    ("oi", False),
    ("volume", False),
    ("bid", True),
    ("bid_qty", False),
    ("ask", True),
    ("ask_qty", False),
    ("oi_day_high", False),
    ("oi_day_low", False),
)


def paise_to_rupees(value: int) -> float:
    return value / 100.0


def envelope(type_: str, payload: Any) -> dict:
    return {"type": type_, "payload": payload}


# --- GridBlock ---------------------------------------------------------------


def _column_values(arr: np.ndarray, is_price: bool, indices: list[int] | None) -> list:
    subset = arr if indices is None else arr[indices]
    if is_price:
        return [paise_to_rupees(int(v)) for v in subset]
    return [int(v) for v in subset]


def grid_block(block: RawBlock, indices: list[int] | None = None) -> dict:
    """Build a UI GridBlock (rupees for prices) from a RawBlock, optionally subset."""
    return {
        field: _column_values(block.columns[field], is_price, indices)
        for field, is_price in GRID_FIELDS
    }


# --- messages ----------------------------------------------------------------


def market_header(
    underlying: str,
    expiry: str,
    spot_paise: int,
    atm_paise: int,
    vix_paise: int,
    risk_free_rate: float,
    timestamp_unix_ms: int,
    sequence: int,
) -> dict:
    return envelope(
        TYPE_MARKET_HEADER,
        {
            "underlying": underlying,
            "expiry": expiry,
            "spot": paise_to_rupees(spot_paise),
            "atm": paise_to_rupees(atm_paise),
            "vix": paise_to_rupees(vix_paise),
            "risk_free_rate": risk_free_rate,  # decimal (e.g. 0.0691)
            "timestamp": timestamp_unix_ms,
            "sequence": sequence,
        },
    )


def option_grid(
    underlying: str,
    expiry: str,
    strikes_paise: np.ndarray,
    calls: RawBlock,
    puts: RawBlock,
) -> dict:
    """Full keyframe: strikes + per-side GridBlock."""
    return envelope(
        TYPE_OPTION_GRID,
        {
            "underlying": underlying,
            "expiry": expiry,
            "strikes": [paise_to_rupees(int(s)) for s in strikes_paise],
            "calls": grid_block(calls),
            "puts": grid_block(puts),
        },
    )


def _changed_indices(prev: RawBlock, curr: RawBlock) -> list[int]:
    mask = np.zeros(curr.length(), dtype=bool)
    for field, _ in GRID_FIELDS:
        mask |= prev.columns[field] != curr.columns[field]
    return [int(i) for i in np.nonzero(mask)[0]]


def option_grid_delta(
    underlying: str,
    prev_frame: IndexFrame,
    curr_frame: IndexFrame,
) -> dict:
    """Sparse patch: strike indices whose calls or puts changed, with new values."""
    call_idx = _changed_indices(prev_frame.calls, curr_frame.calls)
    put_idx = _changed_indices(prev_frame.puts, curr_frame.puts)
    changed = sorted(set(call_idx) | set(put_idx))
    return envelope(
        TYPE_OPTION_GRID_DELTA,
        {
            "underlying": underlying,
            "sequence": curr_frame.sequence,
            "changed_indices": changed,
            "calls": grid_block(curr_frame.calls, changed),
            "puts": grid_block(curr_frame.puts, changed),
        },
    )


def capture_status(per_underlying: list[dict], global_metrics: dict) -> dict:
    return envelope(
        TYPE_CAPTURE_STATUS,
        {"per_underlying": per_underlying, "global": global_metrics},
    )


def heartbeat(timestamp_unix_ms: int) -> dict:
    return envelope(TYPE_HEARTBEAT, {"ts": timestamp_unix_ms})


def session_status(phase: str, diagnostics: dict | None = None) -> dict:
    return envelope(TYPE_SESSION_STATUS, {"phase": phase, "diagnostics": diagnostics or {}})


def log_line(message: str) -> dict:
    return envelope(TYPE_LOG, {"message": message})


def historical_job_update(state: dict) -> dict:
    return envelope(TYPE_HISTORICAL_JOB_UPDATE, state)


def compression_progress(state: dict) -> dict:
    """EOD zstd compression telemetry for the monitor's progress bar.

    ``state`` carries: ``phase`` (running|done|failed|idle), ``files_done``,
    ``files_total``, ``bytes_done``, ``bytes_total``, ``zst_bytes``, ``ratio``,
    ``current_file``, ``threads``, ``started_at``, ``updated_at``.
    """
    return envelope(TYPE_COMPRESSION_PROGRESS, state)
