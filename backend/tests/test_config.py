"""Tests for env parsing: comma-separated indices, CORS origins, required port."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from app.config import DEFAULT_INDICES, Settings

# Required env for a valid Settings (ports/urls come only from env).
_REQUIRED = {
    "KITE_API_KEY": "key",
    "KITE_API_SECRET": "secret",
    "MARKET_DATA_PATH": "/tmp/md",
    "ARCHIVE_DATA_PATH": "/tmp/md-archive",
    "HTTP_PORT": "9000",
    "FRONTEND_URL": "http://localhost:3000",
}


def _set(monkeypatch, **env):
    # Isolate from any ambient env that could leak into the test.
    for key in [
        *_REQUIRED,
        "INDICES",
        "HTTP_HOST",
        "RISK_FREE_RATE",
        "KITE_TOKEN_BROKER_URL",
        "KITE_TOKEN_BROKER_PASSCODE",
        "KITE_USER_ID",
    ]:
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


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_optional_risk_free_rate_is_unset(monkeypatch, value):
    _set(monkeypatch, RISK_FREE_RATE=value)
    assert _settings().risk_free_rate is None


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


def test_archive_data_path_is_required(monkeypatch):
    _set(monkeypatch)
    monkeypatch.delenv("ARCHIVE_DATA_PATH", raising=False)

    with pytest.raises(ValidationError):
        _settings()


def test_storage_roots_are_loaded_from_env(monkeypatch):
    _set(
        monkeypatch,
        MARKET_DATA_PATH="/srv/dev_stack/market_data_dwndr/data/live",
        ARCHIVE_DATA_PATH="/srv/data/z_market_data",
    )

    settings = _settings()

    assert settings.market_data_path.as_posix() == "/srv/dev_stack/market_data_dwndr/data/live"
    assert settings.archive_data_path.as_posix() == "/srv/data/z_market_data"
    assert settings.indices_dir == settings.market_data_path / "INDICES"
    assert settings.stocks_dir == settings.market_data_path / "STOCKS"


def test_archive_root_must_differ_from_live_root(monkeypatch):
    _set(
        monkeypatch,
        MARKET_DATA_PATH="/srv/data/market-data",
        ARCHIVE_DATA_PATH="/srv/data/market-data",
    )

    with pytest.raises(ValidationError, match="must differ"):
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
    assert _settings().http_host == "127.0.0.1"


def test_totp_secret_is_not_an_application_setting(monkeypatch):
    _set(monkeypatch, KITE_TOTP_SECRET="must-not-be-used")

    assert not hasattr(_settings(), "kite_totp_secret")


def test_token_broker_settings_are_paired_https_and_redacted(monkeypatch):
    _set(
        monkeypatch,
        KITE_TOKEN_BROKER_URL="https://calspread.online/api/kite/token",
        KITE_TOKEN_BROKER_PASSCODE="never-print-this",
        KITE_USER_ID="AB1234",
    )

    settings = _settings()

    assert str(settings.kite_token_broker_url) == "https://calspread.online/api/kite/token"
    assert isinstance(settings.kite_token_broker_passcode, SecretStr)
    assert "never-print-this" not in repr(settings)


@pytest.mark.parametrize(
    "env",
    [
        {"KITE_TOKEN_BROKER_URL": "https://calspread.online/api/kite/token"},
        {"KITE_TOKEN_BROKER_PASSCODE": "secret"},
        {
            "KITE_TOKEN_BROKER_URL": "http://calspread.online/api/kite/token",
            "KITE_TOKEN_BROKER_PASSCODE": "secret",
        },
    ],
)
def test_token_broker_rejects_partial_or_insecure_configuration(monkeypatch, env):
    _set(monkeypatch, **env)

    with pytest.raises(ValidationError):
        _settings()


def test_token_broker_requires_expected_user_identity(monkeypatch):
    _set(
        monkeypatch,
        KITE_TOKEN_BROKER_URL="https://calspread.online/api/kite/token",
        KITE_TOKEN_BROKER_PASSCODE="secret",
    )

    with pytest.raises(ValidationError, match="KITE_USER_ID"):
        _settings()
