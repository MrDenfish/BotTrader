"""Tests for v2.utils.credentials."""

import json
import os
import pytest

from v2.utils.credentials import (
    load_coinbase_key_file,
    load_kraken_key_file,
    resolve_credentials,
)


# ------------------------------------------------------------------
# load_coinbase_key_file
# ------------------------------------------------------------------

def test_load_key_file_valid(tmp_path):
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({
        "name": "organizations/abc/apiKeys/xyz",
        "signing_key": "-----BEGIN EC PRIVATE KEY-----\nfake\n-----END EC PRIVATE KEY-----\n",
    }))
    api_key, api_secret = load_coinbase_key_file(str(key_file))
    assert api_key == "organizations/abc/apiKeys/xyz"
    assert "EC PRIVATE KEY" in api_secret


def test_load_key_file_private_key_field(tmp_path):
    """Falls back to 'privateKey' when 'signing_key' is absent."""
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({
        "name": "org/key123",
        "privateKey": "secret123",
    }))
    api_key, api_secret = load_coinbase_key_file(str(key_file))
    assert api_key == "org/key123"
    assert api_secret == "secret123"


def test_load_key_file_missing_fields(tmp_path):
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({"name": "key-only"}))
    with pytest.raises(ValueError, match="missing"):
        load_coinbase_key_file(str(key_file))


def test_load_key_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_coinbase_key_file("/nonexistent/path/key.json")


def test_load_key_file_invalid_json(tmp_path):
    key_file = tmp_path / "key.json"
    key_file.write_text("not json")
    with pytest.raises(json.JSONDecodeError):
        load_coinbase_key_file(str(key_file))


# ------------------------------------------------------------------
# resolve_credentials
# ------------------------------------------------------------------

def test_resolve_explicit_args():
    """Explicit args take highest priority."""
    key, secret = resolve_credentials(api_key="k", api_secret="s")
    assert key == "k"
    assert secret == "s"


def test_resolve_env_vars(monkeypatch):
    """Env vars are used when explicit args are absent."""
    monkeypatch.setenv("COINBASE_API_KEY", "env_key")
    monkeypatch.setenv("COINBASE_API_SECRET", "env_secret")
    key, secret = resolve_credentials()
    assert key == "env_key"
    assert secret == "env_secret"


def test_resolve_custom_env_vars(monkeypatch):
    """Custom env var names work."""
    monkeypatch.setenv("MY_KEY", "custom_key")
    monkeypatch.setenv("MY_SECRET", "custom_secret")
    key, secret = resolve_credentials(api_key_env="MY_KEY", api_secret_env="MY_SECRET")
    assert key == "custom_key"
    assert secret == "custom_secret"


def test_resolve_key_file_fallback(tmp_path, monkeypatch):
    """Key file is used when explicit args and env vars are empty."""
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({
        "name": "file_key",
        "signing_key": "file_secret",
    }))
    key, secret = resolve_credentials(key_file=str(key_file))
    assert key == "file_key"
    assert secret == "file_secret"


def test_resolve_explicit_overrides_env(monkeypatch):
    """Explicit args beat env vars."""
    monkeypatch.setenv("COINBASE_API_KEY", "env_key")
    monkeypatch.setenv("COINBASE_API_SECRET", "env_secret")
    key, secret = resolve_credentials(api_key="explicit_key", api_secret="explicit_secret")
    assert key == "explicit_key"
    assert secret == "explicit_secret"


def test_resolve_env_overrides_key_file(tmp_path, monkeypatch):
    """Env vars beat key file."""
    monkeypatch.setenv("COINBASE_API_KEY", "env_key")
    monkeypatch.setenv("COINBASE_API_SECRET", "env_secret")
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({
        "name": "file_key",
        "signing_key": "file_secret",
    }))
    key, secret = resolve_credentials(key_file=str(key_file))
    assert key == "env_key"
    assert secret == "env_secret"


def test_resolve_missing_key_file_returns_empty(monkeypatch):
    """Bad key file path returns empty strings (no crash)."""
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    key, secret = resolve_credentials(key_file="/nonexistent/key.json")
    assert key == ""
    assert secret == ""


def test_resolve_no_sources(monkeypatch):
    """No credentials anywhere returns empty strings."""
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    key, secret = resolve_credentials()
    assert key == ""
    assert secret == ""


# ------------------------------------------------------------------
# load_kraken_key_file
# ------------------------------------------------------------------

def test_load_kraken_key_file_valid(tmp_path):
    key_file = tmp_path / "kraken.json"
    key_file.write_text(json.dumps({
        "api_key": "kraken_key_123",
        "api_secret": "a3Jha2VuX3NlY3JldA==",
        "rest_api_url": "https://api.kraken.com",
    }))
    api_key, api_secret = load_kraken_key_file(str(key_file))
    assert api_key == "kraken_key_123"
    assert api_secret == "a3Jha2VuX3NlY3JldA=="


def test_load_kraken_key_file_missing_fields(tmp_path):
    key_file = tmp_path / "kraken.json"
    key_file.write_text(json.dumps({"api_key": "only_key"}))
    with pytest.raises(ValueError, match="missing"):
        load_kraken_key_file(str(key_file))


def test_load_kraken_key_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_kraken_key_file("/nonexistent/kraken.json")


def test_load_kraken_key_file_invalid_json(tmp_path):
    key_file = tmp_path / "kraken.json"
    key_file.write_text("not json")
    with pytest.raises(json.JSONDecodeError):
        load_kraken_key_file(str(key_file))


# ------------------------------------------------------------------
# resolve_credentials with key_file_format
# ------------------------------------------------------------------

def test_resolve_kraken_key_file(tmp_path, monkeypatch):
    """Kraken key file format loads correctly."""
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    key_file = tmp_path / "kraken.json"
    key_file.write_text(json.dumps({
        "api_key": "kraken_key",
        "api_secret": "kraken_secret",
    }))
    key, secret = resolve_credentials(
        api_key_env="KRAKEN_API_KEY",
        api_secret_env="KRAKEN_API_SECRET",
        key_file=str(key_file),
        key_file_format="kraken",
    )
    assert key == "kraken_key"
    assert secret == "kraken_secret"


def test_resolve_kraken_env_overrides_key_file(tmp_path, monkeypatch):
    """Env vars beat key file for Kraken too."""
    monkeypatch.setenv("KRAKEN_API_KEY", "env_key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "env_secret")
    key_file = tmp_path / "kraken.json"
    key_file.write_text(json.dumps({
        "api_key": "file_key",
        "api_secret": "file_secret",
    }))
    key, secret = resolve_credentials(
        api_key_env="KRAKEN_API_KEY",
        api_secret_env="KRAKEN_API_SECRET",
        key_file=str(key_file),
        key_file_format="kraken",
    )
    assert key == "env_key"
    assert secret == "env_secret"


def test_resolve_default_format_is_coinbase(tmp_path, monkeypatch):
    """Default key_file_format is coinbase (backward compat)."""
    monkeypatch.delenv("COINBASE_API_KEY", raising=False)
    monkeypatch.delenv("COINBASE_API_SECRET", raising=False)
    key_file = tmp_path / "key.json"
    key_file.write_text(json.dumps({
        "name": "coinbase_key",
        "signing_key": "coinbase_secret",
    }))
    key, secret = resolve_credentials(key_file=str(key_file))
    assert key == "coinbase_key"
    assert secret == "coinbase_secret"
