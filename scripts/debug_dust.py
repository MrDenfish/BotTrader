#!/usr/bin/env python3
"""Debug script to investigate dust detection"""

import asyncio
import aiohttp
import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from Api_manager.coinbase_api import CoinbaseAPI
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.utility import SharedUtility
from Config.config_manager import CentralConfig as Config

async def debug_accounts():
    log_config = {'log_level': 30}  # WARNING level to reduce noise
    logger_manager = LoggerManager(log_config)
    logger = logger_manager.loggers['shared_logger']
    config = Config()

    async with aiohttp.ClientSession() as session:
        shared_utils = SharedUtility.get_instance(logger_manager)
        api = CoinbaseAPI(session, shared_utils, logger_manager, None)

        # Get all accounts
        accounts = await api.get_accounts()

        # Filter to non-zero, non-excluded
        excluded = {'USD', 'USDC', 'USDT', 'BTC'}

        non_zero_balances = []
        for account in accounts:
            currency = account.get('currency', '')
            if currency in excluded:
                continue

            available = account.get('available_balance', {})
            balance_str = available.get('value', '0')

            try:
                balance = Decimal(balance_str)
            except:
                continue

            if balance > 0:
                non_zero_balances.append({
                    'currency': currency,
                    'balance': balance,
                    'balance_str': balance_str
                })

        print(f'\n===== DUST DEBUG REPORT =====')
        print(f'Total accounts: {len(accounts)}')
        print(f'Non-zero balances (excluding BTC/stablecoins): {len(non_zero_balances)}')
        print(f'\nTop 30 balances by amount:')
        for item in sorted(non_zero_balances, key=lambda x: x['balance'], reverse=True)[:30]:
            print(f"  {item['currency']:10s}: {str(item['balance']):>25s}")

        # Now try to get prices
        print(f'\n\n===== ATTEMPTING TO GET USD PRICES =====')
        currencies = [item['currency'] for item in non_zero_balances[:10]]
        for currency in currencies:
            try:
                product_id = f"{currency}-USD"
                result = await api.get_best_bid_ask([product_id])

                if product_id in result and 'ask' in result[product_id]:
                    ask_price = result[product_id]['ask']
                    print(f"  {currency:10s}: ${ask_price}")
                else:
                    print(f"  {currency:10s}: NO PRICE DATA")
            except Exception as e:
                print(f"  {currency:10s}: ERROR - {e}")

asyncio.run(debug_accounts())
