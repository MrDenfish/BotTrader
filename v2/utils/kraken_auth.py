"""Kraken REST API authentication utilities.

Kraken uses HMAC-SHA512 signatures (very different from Coinbase JWT).
All private endpoints require an ``API-Key`` header and an ``API-Sign``
header containing the base64-encoded HMAC digest.

Shared by the exchange adapter, data provider (for WS token), and
pair discovery (if private endpoints are ever needed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse


def kraken_nonce() -> str:
    """Generate a monotonic nonce (millisecond timestamp)."""
    return str(int(time.time() * 1000))


def kraken_signature(api_secret: str, url_path: str, data: dict) -> str:
    """Generate the ``API-Sign`` header value for a Kraken REST request.

    Parameters
    ----------
    api_secret : str
        Base64-encoded API secret from Kraken.
    url_path : str
        The URL path (e.g. ``/0/private/Balance``).
    data : dict
        The POST body as a dict — **must** include a ``"nonce"`` key.

    Returns
    -------
    str
        Base64-encoded HMAC-SHA512 signature.
    """
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode("utf-8")
    message = url_path.encode("utf-8") + hashlib.sha256(encoded).digest()
    secret = base64.b64decode(api_secret)
    mac = hmac.new(secret, message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode("utf-8")
