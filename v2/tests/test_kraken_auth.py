"""Tests for v2.utils.kraken_auth."""

import base64
import hashlib
import hmac
import urllib.parse

import pytest

from v2.utils.kraken_auth import kraken_nonce, kraken_signature


class TestKrakenNonce:
    def test_nonce_is_string(self):
        nonce = kraken_nonce()
        assert isinstance(nonce, str)

    def test_nonce_is_numeric(self):
        nonce = kraken_nonce()
        assert nonce.isdigit()

    def test_nonce_is_monotonic(self):
        """Successive nonces should be non-decreasing."""
        n1 = int(kraken_nonce())
        n2 = int(kraken_nonce())
        assert n2 >= n1

    def test_nonce_is_millisecond_scale(self):
        nonce = int(kraken_nonce())
        # Should be roughly current epoch in milliseconds (> 1.7 trillion)
        assert nonce > 1_700_000_000_000


class TestKrakenSignature:
    # Known test vector: we manually compute the expected signature
    # using the same algorithm to verify our implementation.
    API_SECRET = base64.b64encode(b"supersecretkey12345678901234567890").decode()
    URL_PATH = "/0/private/Balance"
    DATA = {"nonce": "1234567890123"}

    def _expected_signature(self, secret_b64, url_path, data):
        """Reference implementation for comparison."""
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode("utf-8")
        message = url_path.encode("utf-8") + hashlib.sha256(encoded).digest()
        secret = base64.b64decode(secret_b64)
        mac = hmac.new(secret, message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def test_signature_matches_reference(self):
        result = kraken_signature(self.API_SECRET, self.URL_PATH, self.DATA)
        expected = self._expected_signature(self.API_SECRET, self.URL_PATH, self.DATA)
        assert result == expected

    def test_signature_is_base64(self):
        result = kraken_signature(self.API_SECRET, self.URL_PATH, self.DATA)
        # Should decode without error
        decoded = base64.b64decode(result)
        # HMAC-SHA512 produces 64 bytes
        assert len(decoded) == 64

    def test_different_nonce_different_signature(self):
        sig1 = kraken_signature(self.API_SECRET, self.URL_PATH, {"nonce": "111"})
        sig2 = kraken_signature(self.API_SECRET, self.URL_PATH, {"nonce": "222"})
        assert sig1 != sig2

    def test_different_path_different_signature(self):
        sig1 = kraken_signature(self.API_SECRET, "/0/private/Balance", self.DATA)
        sig2 = kraken_signature(self.API_SECRET, "/0/private/AddOrder", self.DATA)
        assert sig1 != sig2

    def test_additional_data_fields(self):
        """Signature changes when extra POST data is added."""
        data1 = {"nonce": "123"}
        data2 = {"nonce": "123", "pair": "XBTUSD", "type": "buy"}
        sig1 = kraken_signature(self.API_SECRET, self.URL_PATH, data1)
        sig2 = kraken_signature(self.API_SECRET, self.URL_PATH, data2)
        assert sig1 != sig2

    def test_known_output_stability(self):
        """Verify the same inputs always produce the same output."""
        sig1 = kraken_signature(self.API_SECRET, self.URL_PATH, dict(self.DATA))
        sig2 = kraken_signature(self.API_SECRET, self.URL_PATH, dict(self.DATA))
        assert sig1 == sig2
