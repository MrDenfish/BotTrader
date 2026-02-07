"""Tests for v2.core.config.Config."""

import os
import tempfile

import pytest

from v2.core.config import Config, PluginRef


class TestConfigLoad:
    def test_defaults_when_no_file(self):
        cfg = Config.load()

        assert cfg.app.name == "BotTrader"
        assert cfg.app.mode == "live"
        assert cfg.app.symbols == ["BTC-USD"]
        assert cfg.exchange.type == "paper"

    def test_load_from_yaml(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
app:
  name: TestBot
  mode: backtest
  symbols:
    - ETH-USD
    - BTC-USD

exchange:
  type: backtest

strategies:
  - type: hybrid_4h_maker
    score_threshold: 2.5

storage:
  type: sqlite
  db_path: test.db
""")
        cfg = Config.load(config_file)

        assert cfg.app.name == "TestBot"
        assert cfg.app.mode == "backtest"
        assert cfg.app.symbols == ["ETH-USD", "BTC-USD"]
        assert cfg.exchange.type == "backtest"
        assert len(cfg.strategies) == 1
        assert cfg.strategies[0].type == "hybrid_4h_maker"
        assert cfg.strategies[0].config["score_threshold"] == 2.5
        assert cfg.storage.type == "sqlite"
        assert cfg.storage.config["db_path"] == "test.db"

    def test_overrides_take_precedence(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
app:
  mode: live
""")
        cfg = Config.load(config_file, overrides={"app": {"mode": "paper"}})

        assert cfg.app.mode == "paper"


class TestEnvVarExpansion:
    def test_env_var_expanded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_API_KEY", "secret123")

        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
exchange:
  type: coinbase
  api_key_env: TEST_API_KEY
""")
        cfg = Config.load(config_file)

        assert cfg.exchange.type == "coinbase"
        assert cfg.exchange.config.get("api_key") == "secret123"

    def test_env_var_missing_is_omitted(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
exchange:
  type: coinbase
  api_key_env: NONEXISTENT_KEY_12345
""")
        cfg = Config.load(config_file)

        assert "api_key" not in cfg.exchange.config


class TestPluginRef:
    def test_string_shorthand(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
data_providers:
  - websocket

observers:
  - structured_log
""")
        cfg = Config.load(config_file)

        assert len(cfg.data_providers) == 1
        assert cfg.data_providers[0].type == "websocket"
        assert cfg.observers[0].type == "structured_log"

    def test_list_plugins(self, tmp_path):
        config_file = tmp_path / "test.yaml"
        config_file.write_text("""
observers:
  - type: structured_log
    level: DEBUG
  - type: daily_report
    email_to: test@test.com
""")
        cfg = Config.load(config_file)

        assert len(cfg.observers) == 2
        assert cfg.observers[0].type == "structured_log"
        assert cfg.observers[0].config["level"] == "DEBUG"
        assert cfg.observers[1].type == "daily_report"
        assert cfg.observers[1].config["email_to"] == "test@test.com"
