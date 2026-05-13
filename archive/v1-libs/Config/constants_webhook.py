"""
Webhook service constants.
"""
import os

# ============================================================================
# Webhook Configuration
# ============================================================================

WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', '5003'))
"""Port for webhook service"""

WEBHOOK_PATH = os.getenv('WEBHOOK_PATH', '/webhook')
"""Base path for webhook endpoints"""

WEBHOOK_TOKEN = os.getenv('WEBHOOK_TOKEN', '')
"""Authentication token for webhook requests"""

# ============================================================================
# Security
# ============================================================================

# IP whitelist (from env)
_whitelist = os.getenv('COIN_WHITELIST', '')
COIN_WHITELIST = [s.strip() for s in _whitelist.split(',') if s.strip()]
"""Allowed IP addresses for webhook requests"""