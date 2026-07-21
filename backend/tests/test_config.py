"""Tests for env parsing: comma-separated indices, CORS origins, required port."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_INDICES, Settings

# Required env for a valid Settings (ports/urls come only from env).
_REQUIRED = {
    "KITE_API_KEY": "key",
    "KITE_API_SECRET": "secret",
    "MARKET_DATA_PATH": "/tmp/md",
    "HTTP_PORT": "9000",
    "FRONTEND_URL": "http://localhost:3000",
}


def _set(monkeypatch, **env):
    # Isolate from any ambient env that could leak into the test.
    for key in [*_REQUIRED, "INDICES", "HTTP_HOST"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_REQUIRED, **env}.items():
        monkeypatch.setenv(key, value)


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_indices_parsed_from_comma_env(monkeypatch):
    _set(monkeypatch, INDICES="NIFTY,BANKNIFTY , finnifty")
    s = _settings()
    # The bug: pydantic-settings JSON-decoded the list before the validator. NoDecode
    # keeps the raw string so the comma-split validator runs.
    assert s.indices == ["NIFTY", "BANKNIFTY", "FINNIFTY"]


def test_indices_default_when_unset(monkeypatch):
    _set(monkeypatch)  # no INDICES
    assert _settings().indices == DEFAULT_INDICES


def test_http_port_from_env_no_default(monkeypatch):
    _set(monkeypatch, HTTP_PORT="9000")
    assert _settings().http_port == 9000


def test_http_port_is_required(monkeypatch):
    _set(monkeypatch)
    monkeypatch.delenv("HTTP_PORT", raising=False)
    with pytest.raises(ValidationError):
        _settings()


def test_frontend_url_is_required(monkeypatch):
    _set(monkeypatch)
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    with pytest.raises(ValidationError):
        _settings()


def test_cors_origins_single_and_multiple(monkeypatch):
    _set(monkeypatch, FRONTEND_URL="http://localhost:3000")
    assert _settings().cors_origins == ["http://localhost:3000"]

    _set(monkeypatch, FRONTEND_URL="http://localhost:3000, https://app.example.com")
    assert _settings().cors_origins == [
        "http://localhost:3000",
        "https://app.example.com",
    ]


def test_http_host_defaults(monkeypatch):
    _set(monkeypatch)
    assert _settings().http_host == "0.0.0.0"
