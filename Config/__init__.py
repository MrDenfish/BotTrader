"""
Configuration package for BotTrader.

Provides centralized access to all constants and environment configuration.

Usage:
    from Config import constants_trading as trading
    print(trading.ATR_WINDOW)

    from Config.environment import env
    print(f"Running in {env.env_name} mode")
"""

# Auto-load environment on package import
from Config.environment import env, is_docker, env_name

# Make constants modules easily accessible
from Config import constants_core
from Config import constants_trading
from Config import constants_report
from Config import constants_webhook
from Config import constants_sighook

__all__ = [
    'env',
    'is_docker',
    'env_name',
    'constants_core',
    'constants_trading',
    'constants_report',
    'constants_webhook',
    'constants_sighook',
]