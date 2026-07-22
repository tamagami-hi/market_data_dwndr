"""Display-ready, on-demand snapshots of the persisted stock L5 matrix."""

from __future__ import annotations

from app.bin_codec.layout import DEPTH_LEVELS, InstrColumns


def _rupees(paise: int) -> float:
    return int(paise) / 100.0


def depth_levels(instr: InstrColumns, row: int) -> list[dict]:
    return [
        {
            "level": level + 1,
            "bid_price": _rupees(instr.depth[level]["bid_price"][row]),
            "bid_qty": int(instr.depth[level]["bid_qty"][row]),
            "bid_orders": int(instr.depth[level]["bid_orders"][row]),
            "ask_price": _rupees(instr.depth[level]["ask_price"][row]),
            "ask_qty": int(instr.depth[level]["ask_qty"][row]),
            "ask_orders": int(instr.depth[level]["ask_orders"][row]),
        }
        for level in range(DEPTH_LEVELS)
    ]


def stock_depth_snapshot(matrix, symbol: str) -> dict | None:
    """Return current L5 for one symbol without copying/broadcasting the whole board."""
    requested = symbol.strip().upper()
    row = next(
        (
            index
            for index, ref in enumerate(matrix.stock_refs)
            if requested in {ref.tradingsymbol.upper(), ref.name.upper()}
        ),
        None,
    )
    if row is None:
        return None
    ref = matrix.stock_refs[row]
    leg_names = ("fut_current", "fut_mid", "fut_far")
    futures = [
        {
            "label": label,
            "expiry": future.expiry,
            "depth": depth_levels(matrix.legs[leg_names[index]], row),
        }
        for index, (label, future) in enumerate(
            zip(("Current future", "Mid future", "Far future"), ref.futures, strict=False)
        )
    ]
    return {
        "tradingsymbol": ref.tradingsymbol,
        "name": ref.name,
        "spot_depth": depth_levels(matrix.legs["spot"], row),
        "futures": futures,
    }
