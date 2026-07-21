"""Kite LTP quote client — used to seed the ATM strike at bootstrap.

The option-chain assembler needs the current **spot** to pick the ATM ± 50 window, so
before the tick stream is live we fetch a one-shot LTP quote for each index spot symbol
(and India VIX) via ``GET https://api.kite.trade/quote/ltp?i=…``. The same static-IP /
proxy-aware client used for login is reused so the call egresses from the whitelisted IP.

The HTTP call is injected so bootstrap can be unit-tested without the network.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.kite.auth import auth_header

KITE_API_BASE = "https://api.kite.trade"

# symbols -> {symbol: last_price}
QuoteFn = Callable[[Iterable[str]], dict[str, float]]


def fetch_ltp(client, api_key: str, access_token: str, symbols: Iterable[str]) -> dict[str, float]:
    """Fetch last-traded prices for ``symbols`` (e.g. ``"NSE:NIFTY 50"``).

    ``client`` is an ``httpx.Client``-like object with ``get(url, params=, headers=)``.
    Returns ``{symbol: last_price}``; symbols Kite doesn't recognise are omitted.
    """
    symbols = list(symbols)
    if not symbols:
        return {}
    resp = client.get(
        f"{KITE_API_BASE}/quote/ltp",
        params=[("i", s) for s in symbols],
        headers=auth_header(api_key, access_token),
    )
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"quote/ltp: non-JSON response ({resp.status_code})") from exc
    if body.get("status") != "success":
        message = body.get("message") or body.get("error_type") or "unknown error"
        raise RuntimeError(f"quote/ltp failed: {message}")
    data = body.get("data") or {}
    out: dict[str, float] = {}
    for symbol, payload in data.items():
        if isinstance(payload, dict) and payload.get("last_price") is not None:
            out[symbol] = float(payload["last_price"])
    return out


def default_quote_fn(settings, access_token: str) -> QuoteFn:
    """Build a network-backed ``QuoteFn`` using the static-IP/proxy-aware client."""

    def _quote(symbols: Iterable[str]) -> dict[str, float]:
        from app.kite.login import build_kite_http_client

        client = build_kite_http_client(settings.kite_static_ip, settings.kite_http_proxy)
        try:
            return fetch_ltp(client, settings.kite_api_key, access_token, symbols)
        finally:
            client.close()

    return _quote
