"""Layered configuration loader.

Priority (highest wins): CLI overrides → env vars → config file → defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config sections
# ---------------------------------------------------------------------------

@dataclass
class AppConfig:
    name: str = "BotTrader"
    mode: str = "live"  # "live", "paper", "backtest"
    symbols: list[str] = field(default_factory=lambda: ["BTC-USD"])


@dataclass
class PluginRef:
    """Reference to a plugin by type name, plus its config dict."""
    type: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    """Top-level application configuration."""
    app: AppConfig = field(default_factory=AppConfig)
    exchange: PluginRef = field(default_factory=lambda: PluginRef(type="paper"))
    data_providers: list[PluginRef] = field(default_factory=list)
    strategies: list[PluginRef] = field(default_factory=list)
    risk: list[PluginRef] = field(default_factory=lambda: [PluginRef(type="basic")])
    execution: PluginRef = field(default_factory=lambda: PluginRef(type="maker_only"))
    storage: PluginRef = field(default_factory=lambda: PluginRef(type="sqlite"))
    observers: list[PluginRef] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict | None = None) -> Config:
        """Load config from YAML file, apply env-var expansions, then overrides."""
        raw: dict[str, Any] = {}

        # 1. Load from file
        if path:
            p = Path(path)
            if p.exists():
                raw = yaml.safe_load(p.read_text()) or {}

        # 2. Expand env-var references (keys ending in _env)
        _expand_env_vars(raw)

        # 3. Apply CLI / programmatic overrides
        if overrides:
            _deep_merge(raw, overrides)

        return cls._from_dict(raw)

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Config:
        cfg = cls()

        # App
        if "app" in d:
            app = d["app"]
            cfg.app = AppConfig(
                name=app.get("name", cfg.app.name),
                mode=app.get("mode", cfg.app.mode),
                symbols=app.get("symbols", cfg.app.symbols),
            )

        # Exchange
        if "exchange" in d:
            cfg.exchange = _parse_plugin_ref(d["exchange"])

        # List-of-plugin sections
        if "data_providers" in d:
            cfg.data_providers = [_parse_plugin_ref(x) for x in d["data_providers"]]

        if "strategies" in d:
            cfg.strategies = [_parse_plugin_ref(x) for x in d["strategies"]]

        if "observers" in d:
            cfg.observers = [_parse_plugin_ref(x) for x in d["observers"]]

        # Risk: supports both single dict (backward compat) and list
        if "risk" in d:
            raw_risk = d["risk"]
            if isinstance(raw_risk, list):
                cfg.risk = [_parse_plugin_ref(x) for x in raw_risk]
            else:
                cfg.risk = [_parse_plugin_ref(raw_risk)]

        if "execution" in d:
            cfg.execution = _parse_plugin_ref(d["execution"])

        if "storage" in d:
            cfg.storage = _parse_plugin_ref(d["storage"])

        return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_plugin_ref(raw: dict[str, Any] | str) -> PluginRef:
    """Parse a plugin reference from either a dict or just a type string."""
    if isinstance(raw, str):
        return PluginRef(type=raw)
    plugin_type = raw.pop("type", "")
    return PluginRef(type=plugin_type, config=raw)


def _expand_env_vars(d: dict) -> None:
    """Recursively replace keys ending in ``_env`` with their env-var values.

    Example: ``{"api_key_env": "COINBASE_API_KEY"}`` becomes
    ``{"api_key": "<value from env>"}`` if the env var is set.
    """
    keys_to_process = [k for k in d if isinstance(k, str) and k.endswith("_env")]
    for key in keys_to_process:
        env_var = d.pop(key)
        base_key = key[:-4]  # strip "_env"
        value = os.environ.get(env_var)
        if value is not None:
            d[base_key] = value

    for v in d.values():
        if isinstance(v, dict):
            _expand_env_vars(v)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _expand_env_vars(item)


def _deep_merge(base: dict, overrides: dict) -> None:
    """Merge *overrides* into *base* in place (recursive)."""
    for k, v in overrides.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
