"""Shared credential loading for exchange API plugins."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def load_coinbase_key_file(path: str) -> tuple[str, str]:
    """Load API key + secret from a Coinbase CDP JSON key file.

    Returns ``(api_key, api_secret)``.
    Raises ``FileNotFoundError`` if *path* does not exist and
    ``ValueError`` if the JSON is missing required fields.
    """
    with open(path) as f:
        data = json.load(f)
    api_key = data.get("name", "")
    api_secret = data.get("signing_key", "") or data.get("privateKey", "")
    if not api_key or not api_secret:
        raise ValueError(f"Key file {path} missing 'name' or 'signing_key'/'privateKey'")
    logger.info("Loaded API credentials from %s (key_len=%d)", path, len(api_key))
    return api_key, api_secret


def load_kraken_key_file(path: str) -> tuple[str, str]:
    """Load API key + secret from a Kraken JSON key file.

    Expected format::

        {
            "api_key": "<KRAKEN_API_KEY>",
            "api_secret": "<KRAKEN_API_SECRET>",
            ...
        }

    Returns ``(api_key, api_secret)``.
    Raises ``FileNotFoundError`` if *path* does not exist and
    ``ValueError`` if the JSON is missing required fields.
    """
    with open(path) as f:
        data = json.load(f)
    api_key = data.get("api_key", "")
    api_secret = data.get("api_secret", "")
    if not api_key or not api_secret:
        raise ValueError(f"Key file {path} missing 'api_key' or 'api_secret'")
    logger.info("Loaded Kraken credentials from %s (key_len=%d)", path, len(api_key))
    return api_key, api_secret


_KEY_FILE_LOADERS = {
    "coinbase": load_coinbase_key_file,
    "kraken": load_kraken_key_file,
}


def resolve_credentials(
    api_key: str | None = None,
    api_secret: str | None = None,
    api_key_env: str = "COINBASE_API_KEY",
    api_secret_env: str = "COINBASE_API_SECRET",
    key_file: str | None = None,
    key_file_format: str = "coinbase",
) -> tuple[str, str]:
    """Resolve exchange credentials with priority: explicit > env var > key_file.

    *key_file_format* selects the loader for the key file (``"coinbase"`` or
    ``"kraken"``).  Defaults to ``"coinbase"`` for backward compatibility.

    Returns ``(api_key, api_secret)``.  Falls back to empty strings if
    no source provides credentials.
    """
    # 1. Explicit arguments
    resolved_key = api_key or ""
    resolved_secret = api_secret or ""

    # 2. Environment variables
    if not resolved_key:
        resolved_key = os.environ.get(api_key_env, "")
    if not resolved_secret:
        resolved_secret = os.environ.get(api_secret_env, "")

    # 3. Key file fallback
    if not resolved_key and not resolved_secret and key_file:
        loader = _KEY_FILE_LOADERS.get(key_file_format, load_coinbase_key_file)
        try:
            resolved_key, resolved_secret = loader(key_file)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to load key file %s: %s", key_file, e)

    return resolved_key, resolved_secret
