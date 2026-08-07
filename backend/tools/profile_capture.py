"""Measure the 1 Hz capture hot path at production scale.

Run before deciding to optimise anything (§28: measure, don't guess):

    python -m tools.profile_capture

Builds live tables at the real deployed sizes — four index option chains at ATM±50, the
whole F&O stock board, and the new consolidated index-F&O dataset — then times the three
things that actually run every second:

1. ``apply_ticks``      — routing a tick batch into the tables (runs continuously)
2. ``capture_snapshot`` — copying every table into frames (runs once per grid second)
3. ``encode + write``   — the writer threads' per-frame cost

The 1 Hz grid gives each snapshot a full second of budget, so the numbers to watch are the
*fraction of that second* consumed and the projected daily disk growth. Nothing here
changes behaviour; it only reports.
"""

from __future__ import annotations

import gc
import statistics
import time
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from app.bin_codec.writer import (
    IndexBinWriter,
    IndexFnoBinWriter,
    StockBinWriter,
    encode_index_fno_frame,
    encode_index_frame,
    encode_stock_frame,
)
from app.chain.assembler import OptionChain
from app.chain.table import IndexTable
from app.index_fno.board import IndexFnoEntry
from app.index_fno.matrix import IndexFnoMatrix
from app.stocks.board import BoardEntry
from app.stocks.matrix import StockMatrix

# Deployed shape, from the live instrument dumps and the verified 2,067-token subscription.
N_INDICES = 6
N_STRIKES = 101  # ATM +/- 50
N_STOCKS = 208
N_FUTURES = 3
GRID_SECONDS_PER_DAY = 22_500  # the equity-derivatives session, 09:15-15:30
TICKS_PER_SECOND = 700  # observed rate on the deployment
ITERATIONS = 200


def _instrument(token: int, symbol: str, expiry: str = "2026-08-27"):
    from app.kite.instruments import Instrument

    return Instrument(
        instrument_token=token,
        exchange_token=token // 256,
        tradingsymbol=symbol,
        name=symbol,
        last_price=0.0,
        expiry=expiry,
        strike=0.0,
        tick_size=0.05,
        lot_size=50,
        instrument_type="FUT",
        segment="NFO-FUT",
        exchange="NFO",
    )


def _index_table(underlying: str, base: int) -> IndexTable:
    strikes = np.array(
        [24_000_00 + i * 50_00 for i in range(N_STRIKES)], dtype="<i8"
    )  # paise
    calls = np.array([base + i for i in range(N_STRIKES)], dtype="<u8")
    puts = np.array([base + N_STRIKES + i for i in range(N_STRIKES)], dtype="<u8")
    chain = OptionChain(
        underlying=underlying,
        expiry="2026-08-27",
        reference_spot_paise=2_450_000,
        atm_paise=2_450_000,
        strikes=strikes,
        call_tokens=calls,
        put_tokens=puts,
        token_map={},
    )
    from app.chain.assembler import ROLE_OPTION, Role

    chain.token_map = {
        **{int(t): Role(ROLE_OPTION, side="CE", index=i) for i, t in enumerate(calls)},
        **{int(t): Role(ROLE_OPTION, side="PE", index=i) for i, t in enumerate(puts)},
    }
    return IndexTable(chain, 0.0691, "2026-08-10")


def _stock_matrix() -> StockMatrix:
    board = [
        BoardEntry(
            name=f"STK{row}",
            spot=_instrument(500_000 + row * 10, f"STK{row}", expiry=""),
            futures=[
                _instrument(500_000 + row * 10 + 1 + f, f"STK{row}FUT{f}")
                for f in range(N_FUTURES)
            ],
        )
        for row in range(N_STOCKS)
    ]
    return StockMatrix(board, 0.0691, "2026-08-10")


def _index_fno_matrix() -> IndexFnoMatrix:
    board = [
        IndexFnoEntry(
            underlying=f"IDX{row}",
            spot_symbol=f"NSE:IDX{row}",
            spot_token=900_000 + row * 10,
            futures=[
                _instrument(900_000 + row * 10 + 1 + f, f"IDX{row}FUT{f}")
                for f in range(N_FUTURES)
            ],
        )
        for row in range(N_INDICES)
    ]
    return IndexFnoMatrix(board, 0.0691, "2026-08-10")


def _timed(label: str, fn, iterations: int = ITERATIONS) -> float:
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    median = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    print(f"  {label:<42} median {median:7.3f} ms   p95 {p95:7.3f} ms")
    return median


def main() -> None:
    print("Building live tables at deployed scale...")
    tracemalloc.start()
    tables = {
        name: _index_table(name, 100_000 + i * 10_000)
        for i, name in enumerate(
            ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"]
        )
    }
    stocks = _stock_matrix()
    index_fno = _index_fno_matrix()
    gc.collect()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"  live state: {current / 1e6:.1f} MB resident, {peak / 1e6:.1f} MB peak"
        f"  ({N_INDICES} chains x {N_STRIKES} strikes, {N_STOCKS} stocks, "
        f"{N_INDICES} index-F&O rows)"
    )

    # 1. Tick application — the continuous path.
    batch = [
        {
            "instrument_token": int(tables["NIFTY"].chain.call_tokens[i % N_STRIKES]),
            "last_price": 100.0 + i,
            "volume_traded": i,
            "oi": i * 2,
        }
        for i in range(TICKS_PER_SECOND)
    ]
    print(f"\napply_ticks ({TICKS_PER_SECOND} ticks = one second of production flow):")

    def _apply_batch() -> None:
        table = tables["NIFTY"]
        for tick in batch:
            table.apply_tick(tick)

    _timed("route + write into tables", _apply_batch)

    # 2. Snapshot — the 1 Hz path, all domains.
    ts = 1_786_000_000_000
    print("\ncapture_snapshot (copy-on-write, per grid second):")
    per_index = _timed("one index chain", lambda: tables["NIFTY"].snapshot(ts))
    stock_ms = _timed("stock matrix (all F&O stocks, L5)", lambda: stocks.snapshot(ts))
    fno_ms = _timed("index-F&O matrix (all indices, L5)", lambda: index_fno.snapshot(ts))
    total_snapshot = per_index * N_INDICES + stock_ms + fno_ms
    print(f"  {'ALL DOMAINS per grid second':<42} {total_snapshot:7.3f} ms"
          f"   = {total_snapshot / 10:.2f}% of the 1 s budget")

    # 3. Encode + write.
    index_frame = tables["NIFTY"].snapshot(ts)
    stock_frame = stocks.snapshot(ts)
    fno_frame = index_fno.snapshot(ts)
    print("\nencode (bytes produced per frame):")
    index_bytes = len(encode_index_frame(index_frame, N_STRIKES))
    stock_bytes = len(encode_stock_frame(stock_frame, N_STOCKS))
    fno_bytes = len(encode_index_fno_frame(fno_frame, N_INDICES))
    _timed("index frame", lambda: encode_index_frame(index_frame, N_STRIKES))
    _timed("stock frame", lambda: encode_stock_frame(stock_frame, N_STOCKS))
    _timed("index-F&O frame", lambda: encode_index_fno_frame(fno_frame, N_INDICES))
    print(f"  index    {index_bytes:>9,} B/frame -> "
          f"{index_bytes * GRID_SECONDS_PER_DAY / 1e9:6.2f} GB/day x {N_INDICES} files")
    print(f"  stocks   {stock_bytes:>9,} B/frame -> "
          f"{stock_bytes * GRID_SECONDS_PER_DAY / 1e9:6.2f} GB/day")
    print(f"  indexF&O {fno_bytes:>9,} B/frame -> "
          f"{fno_bytes * GRID_SECONDS_PER_DAY / 1e9:6.2f} GB/day  <-- NEW")
    daily = (index_bytes * N_INDICES + stock_bytes + fno_bytes) * GRID_SECONDS_PER_DAY
    print(f"  TOTAL uncompressed {daily / 1e9:.2f} GB/day")

    # 4. Durable write cost, measured on real files.
    print("\nwrite + fsync (per frame, real files):")
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        iw = IndexBinWriter(root / "idx.bin", sync=True)
        iw.open()
        iw.write_header(tables["NIFTY"].header())
        _timed("index frame, fsync per frame", lambda: iw.append_frame(index_frame), 50)
        iw.close()

        fw = IndexFnoBinWriter(root / "fno.bin", sync=True)
        fw.open()
        fw.write_header(index_fno.header())
        _timed("index-F&O frame, fsync per frame", lambda: fw.append_frame(fno_frame), 50)
        fw.close()

        sw = StockBinWriter(root / "stk.bin", sync=True)
        sw.open()
        sw.write_header(stocks.header())
        _timed("stock frame, fsync per frame", lambda: sw.append_frame(stock_frame), 20)
        sw.close()

    print(
        "\nInterpretation: the snapshot path is the only work bound to the 1 Hz grid, and\n"
        "writes happen on separate threads. Compare the ALL DOMAINS figure against 1000 ms\n"
        "before considering any concurrency, batching or rewrite work."
    )


if __name__ == "__main__":
    main()
