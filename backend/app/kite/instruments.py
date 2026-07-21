"""Kite instrument-master dump: fetch, parse, and daily archive.

``GET https://api.kite.trade/instruments/{exchange}`` returns a CSV with columns:
``instrument_token, exchange_token, tradingsymbol, name, last_price, expiry, strike,
tick_size, lot_size, instrument_type, segment, exchange``.

A daily snapshot per exchange is archived under ``_instruments/<date>/<EXCH>.csv`` so
past boards / expired tokens can be reconstructed later
(docs/20-data-and-storage/storage-layout.md). The HTTP fetch is injected so parsing
and archiving can be unit-tested without the network.
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

CSV_COLUMNS = (
    "instrument_token",
    "exchange_token",
    "tradingsymbol",
    "name",
    "last_price",
    "expiry",
    "strike",
    "tick_size",
    "lot_size",
    "instrument_type",
    "segment",
    "exchange",
)


@dataclass(frozen=True)
class Instrument:
    instrument_token: int
    exchange_token: int
    tradingsymbol: str
    name: str
    last_price: float
    expiry: str  # "YYYY-MM-DD" or "" for non-dated instruments
    strike: float  # rupees, as given by Kite
    tick_size: float
    lot_size: int
    instrument_type: str  # EQ, FUT, CE, PE, ...
    segment: str
    exchange: str


def _to_int(value: str) -> int:
    value = value.strip()
    return int(value) if value else 0


def _to_float(value: str) -> float:
    value = value.strip()
    return float(value) if value else 0.0


def parse_instruments_csv(text: str) -> list[Instrument]:
    """Parse a Kite instruments CSV into ``Instrument`` records."""
    reader = csv.DictReader(io.StringIO(text))
    out: list[Instrument] = []
    for row in reader:
        out.append(
            Instrument(
                instrument_token=_to_int(row.get("instrument_token", "")),
                exchange_token=_to_int(row.get("exchange_token", "")),
                tradingsymbol=row.get("tradingsymbol", "").strip(),
                name=row.get("name", "").strip(),
                last_price=_to_float(row.get("last_price", "")),
                expiry=row.get("expiry", "").strip(),
                strike=_to_float(row.get("strike", "")),
                tick_size=_to_float(row.get("tick_size", "")),
                lot_size=_to_int(row.get("lot_size", "")),
                instrument_type=row.get("instrument_type", "").strip(),
                segment=row.get("segment", "").strip(),
                exchange=row.get("exchange", "").strip(),
            )
        )
    return out


# Fetcher: exchange -> raw CSV text.
Fetcher = Callable[[str], str]


def default_http_fetcher(
    base_url: str = "https://api.kite.trade",
    headers: dict[str, str] | None = None,
) -> Fetcher:
    """HTTP fetcher for the public instruments dump (auth optional)."""

    def _fetch(exchange: str) -> str:
        import httpx

        resp = httpx.get(f"{base_url}/instruments/{exchange}", headers=headers, timeout=30.0)
        resp.raise_for_status()
        return resp.text

    return _fetch


class InstrumentStore:
    """Fetches, archives, and loads instrument dumps per exchange per day."""

    def __init__(self, instruments_dir: str | os.PathLike[str], fetcher: Fetcher) -> None:
        self.instruments_dir = Path(instruments_dir)
        self._fetch = fetcher

    def archive_path(self, exchange: str, trading_date: str) -> Path:
        return self.instruments_dir / trading_date / f"{exchange}.csv"

    def is_archived(self, exchange: str, trading_date: str) -> bool:
        return self.archive_path(exchange, trading_date).exists()

    def fetch_and_archive(self, exchange: str, trading_date: str) -> list[Instrument]:
        text = self._fetch(exchange)
        path = self.archive_path(exchange, trading_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return parse_instruments_csv(text)

    def load_archived(self, exchange: str, trading_date: str) -> list[Instrument] | None:
        path = self.archive_path(exchange, trading_date)
        if not path.exists():
            return None
        return parse_instruments_csv(path.read_text(encoding="utf-8"))

    def get(
        self, exchange: str, trading_date: str, refresh: bool = False
    ) -> list[Instrument]:
        """Return instruments for ``exchange``, using the archive unless ``refresh``."""
        if not refresh:
            cached = self.load_archived(exchange, trading_date)
            if cached is not None:
                return cached
        return self.fetch_and_archive(exchange, trading_date)


def filter_by_type(instruments: Iterable[Instrument], instrument_type: str) -> list[Instrument]:
    return [i for i in instruments if i.instrument_type == instrument_type]


def filter_options(
    instruments: Iterable[Instrument], name: str, expiry: str | None = None
) -> list[Instrument]:
    """Return CE/PE contracts for an underlying ``name`` (optionally one expiry)."""
    out = []
    for i in instruments:
        if i.name == name and i.instrument_type in ("CE", "PE"):
            if expiry is None or i.expiry == expiry:
                out.append(i)
    return out
