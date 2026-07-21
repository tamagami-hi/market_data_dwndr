"""Per-index configuration (locked set).

docs/30-live-capture/option-chain-selection.md, decision #9. MIDCPNIFTY and BANKEX
are intentionally excluded.
"""

from __future__ import annotations

from dataclasses import dataclass

# ATM +/- 50 -> up to 101 strikes.
STRIKES_PER_SIDE = 50

# India VIX (raw, stored per chain).
VIX_SYMBOL = "NSE:INDIA VIX"
VIX_TOKEN = 264969


@dataclass(frozen=True)
class IndexConfig:
    underlying: str  # instrument-master ``name`` (e.g. "NIFTY")
    step: int  # ATM strike step in rupees
    options_exchange: str  # NFO or BFO
    spot_symbol: str  # e.g. "NSE:NIFTY 50"
    spot_token: int


INDEX_CONFIGS: dict[str, IndexConfig] = {
    "NIFTY": IndexConfig("NIFTY", 50, "NFO", "NSE:NIFTY 50", 256265),
    "BANKNIFTY": IndexConfig("BANKNIFTY", 100, "NFO", "NSE:NIFTY BANK", 260105),
    "FINNIFTY": IndexConfig("FINNIFTY", 50, "NFO", "NSE:NIFTY FIN SERVICE", 257801),
    "SENSEX": IndexConfig("SENSEX", 100, "BFO", "BSE:SENSEX", 265),
}


def get_index_config(underlying: str) -> IndexConfig:
    key = underlying.upper()
    if key not in INDEX_CONFIGS:
        raise KeyError(f"unknown index '{underlying}'; known: {sorted(INDEX_CONFIGS)}")
    return INDEX_CONFIGS[key]
