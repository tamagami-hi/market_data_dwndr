"""Per-index configuration.

docs/30-live-capture/option-chain-selection.md, decision #9.

MIDCPNIFTY and BANKEX were originally excluded; both were added on 2026-08-07 when the
index-F&O domain was introduced. Every value below was verified against that day's live
instrument dumps rather than assumed:

* spot tokens read from the ``INDICES`` segment of the NSE and BSE masters
  (``NIFTY MID SELECT`` = 288009, ``BANKEX`` = 274441, which also re-confirmed SENSEX 265);
* ``step`` derived from the modal gap between consecutive listed strikes
  (MIDCPNIFTY 25, BANKEX 100), not from the round number it resembles.

A wrong spot token or step would not fail loudly — it would silently produce a chain
centred on the wrong strike — so these are the two fields to re-verify if an exchange ever
relists an index.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    "MIDCPNIFTY": IndexConfig("MIDCPNIFTY", 25, "NFO", "NSE:NIFTY MID SELECT", 288009),
    "SENSEX": IndexConfig("SENSEX", 100, "BFO", "BSE:SENSEX", 265),
    "BANKEX": IndexConfig("BANKEX", 100, "BFO", "BSE:BANKEX", 274441),
}


def get_index_config(underlying: str) -> IndexConfig:
    key = underlying.upper()
    if key not in INDEX_CONFIGS:
        raise KeyError(f"unknown index '{underlying}'; known: {sorted(INDEX_CONFIGS)}")
    return INDEX_CONFIGS[key]
