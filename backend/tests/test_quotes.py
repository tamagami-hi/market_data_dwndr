"""Tests for the Kite LTP quote client."""

from __future__ import annotations

import pytest

from app.kite.quotes import fetch_ltp


class FakeResp:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeClient:
    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        return self.resp


def test_fetch_ltp_parses_prices():
    resp = FakeResp(
        json_body={
            "status": "success",
            "data": {
                "NSE:NIFTY 50": {"instrument_token": 256265, "last_price": 24567.35},
                "NSE:INDIA VIX": {"instrument_token": 264969, "last_price": 12.34},
            },
        }
    )
    client = FakeClient(resp)
    out = fetch_ltp(client, "apikey", "tok", ["NSE:NIFTY 50", "NSE:INDIA VIX"])
    assert out == {"NSE:NIFTY 50": 24567.35, "NSE:INDIA VIX": 12.34}
    # auth header + repeated ?i= params
    url, params, headers = client.calls[0]
    assert url.endswith("/quote/ltp")
    assert params == [("i", "NSE:NIFTY 50"), ("i", "NSE:INDIA VIX")]
    assert headers["Authorization"] == "token apikey:tok"


def test_fetch_ltp_empty_symbols_skips_call():
    client = FakeClient(FakeResp())
    assert fetch_ltp(client, "k", "t", []) == {}
    assert client.calls == []


def test_fetch_ltp_raises_on_error_status():
    client = FakeClient(FakeResp(json_body={"status": "error", "message": "bad token"}))
    with pytest.raises(RuntimeError, match="bad token"):
        fetch_ltp(client, "k", "t", ["NSE:NIFTY 50"])


def test_fetch_ltp_omits_missing_prices():
    resp = FakeResp(
        json_body={"status": "success", "data": {"BSE:SENSEX": {"last_price": None}}}
    )
    assert fetch_ltp(FakeClient(resp), "k", "t", ["BSE:SENSEX"]) == {}
