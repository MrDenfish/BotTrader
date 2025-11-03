"""
Sighook service constants.
"""
import os

# ============================================================================
# Sighook Configuration
# ============================================================================

# Webhook base URL is environment-specific (set in .env)
WEBHOOK_BASE_URL = os.getenv('WEBHOOK_BASE_URL', 'http://webhook:5003')
"""Base URL for webhook service (env-specific)"""

# ============================================================================
# API Configuration
# ============================================================================

PC_URL = os.getenv('PC_URL', 'https://pro.coinbase.com')
"""Coinbase Pro API URL"""

# API key file paths (environment-specific)
API_KEY_SIGHOOK = os.getenv('API_KEY_SIGHOOK', '/app/sighook_api_key.json')
API_KEY_WEBHOOK = os.getenv('API_KEY_WEBHOOK', '/app/webhook_api_key.json')
API_KEY_TB = os.getenv('API_KEY_TB', '/app/tb_api_key.json')
API_KEY_WEBSOCKET = os.getenv('API_KEY_WEBSOCKET', '/app/websocket_api_key.json')