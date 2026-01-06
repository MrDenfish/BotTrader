#!/usr/bin/env python3
"""
Dust to BTC Converter

This script converts small cryptocurrency balances (< $0.50) to BTC.
Designed to run weekly via cron job.

Usage:
    python scripts/convert_dust_to_btc.py [--dry-run]

Options:
    --dry-run    Show what would be converted without executing trades
"""

import asyncio
import aiohttp
import logging
import sys
import os
from decimal import Decimal
from typing import List, Dict
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from Api_manager.coinbase_api import CoinbaseAPI
from Shared_Utils.logging_manager import LoggerManager
from Shared_Utils.precision_manager import PrecisionManager
from Shared_Utils.shared_utils import SharedUtils
from Config.config_manager import CentralConfig as Config


class DustConverter:
    """Converts cryptocurrency dust (small balances) to BTC"""

    def __init__(self, dry_run: bool = False):
        """
        Initialize the dust converter.

        Args:
            dry_run: If True, only show what would be converted without executing
        """
        self.dry_run = dry_run
        self.config = Config()

        # Thresholds
        self.dust_threshold_usd = Decimal("0.50")  # Balances below this are considered dust
        self.min_btc_order_usd = Decimal("1.00")  # Minimum BTC order size in USD
        self.target_currency = "BTC"

        # Currencies to exclude from conversion
        self.excluded_currencies = {
            "USD", "USDC", "USDT",  # Stablecoins
            "BTC",  # Target currency
        }

        # Setup logging
        log_config = {"log_level": logging.INFO}
        self.logger_manager = LoggerManager(log_config)
        self.logger = self.logger_manager.loggers['shared_logger']

        # Setup utilities
        self.session = None
        self.precision_manager = PrecisionManager()
        self.shared_utils = SharedUtils()

        # Will be initialized in run()
        self.coinbase_api = None

    async def initialize(self):
        """Initialize async components"""
        self.session = aiohttp.ClientSession()
        self.coinbase_api = CoinbaseAPI(
            self.session,
            self.shared_utils,
            self.logger_manager,
            self.precision_manager
        )

    async def cleanup(self):
        """Cleanup async resources"""
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_current_prices(self, currencies: List[str]) -> Dict[str, Decimal]:
        """
        Get current USD prices for given currencies.

        Args:
            currencies: List of currency symbols

        Returns:
            Dict mapping currency -> USD price
        """
        prices = {}

        for currency in currencies:
            if currency in {"USD", "USDC", "USDT"}:
                prices[currency] = Decimal("1.0")
                continue

            try:
                # Get best bid/ask for currency-USD pair
                product_id = f"{currency}-USD"
                result = await self.coinbase_api.get_best_bid_ask([product_id])

                if product_id in result and "ask" in result[product_id]:
                    ask_price = result[product_id]["ask"]
                    prices[currency] = Decimal(str(ask_price))
                    self.logger.debug(f"Price for {currency}: ${ask_price}")
                else:
                    self.logger.warning(f"⚠️ Could not get price for {currency}")
                    prices[currency] = Decimal("0")

            except Exception as e:
                self.logger.error(f"❌ Error getting price for {currency}: {e}")
                prices[currency] = Decimal("0")

        return prices

    async def find_dust_balances(self) -> List[Dict]:
        """
        Find all account balances below the dust threshold.

        Returns:
            List of dust balance dicts with keys: currency, balance, usd_value
        """
        self.logger.info("🔍 Fetching account balances...")

        accounts = await self.coinbase_api.get_accounts()

        if not accounts:
            self.logger.warning("⚠️ No accounts found or API call failed")
            return []

        self.logger.info(f"📊 Found {len(accounts)} total accounts")

        # Filter to crypto accounts with available balance
        dust_candidates = []

        for account in accounts:
            currency = account.get("currency", "")

            # Skip excluded currencies
            if currency in self.excluded_currencies:
                continue

            # Get available balance
            available = account.get("available_balance", {})
            balance_str = available.get("value", "0")

            try:
                balance = Decimal(balance_str)
            except:
                continue

            # Skip zero or negative balances
            if balance <= 0:
                continue

            dust_candidates.append({
                "currency": currency,
                "balance": balance,
                "account_uuid": account.get("uuid"),
                "ready": account.get("ready", False),
                "active": account.get("active", False)
            })

        if not dust_candidates:
            self.logger.info("✅ No non-zero balances found (excluding BTC and stablecoins)")
            return []

        self.logger.info(f"📋 Found {len(dust_candidates)} non-zero crypto balances")

        # Get current prices
        currencies = [d["currency"] for d in dust_candidates]
        prices = await self.get_current_prices(currencies)

        # Calculate USD values and filter dust
        dust_balances = []

        for candidate in dust_candidates:
            currency = candidate["currency"]
            balance = candidate["balance"]
            price = prices.get(currency, Decimal("0"))
            usd_value = balance * price

            if Decimal("0") < usd_value < self.dust_threshold_usd:
                dust_balances.append({
                    "currency": currency,
                    "balance": balance,
                    "usd_value": usd_value,
                    "price_usd": price,
                    "account_uuid": candidate["account_uuid"],
                    "ready": candidate["ready"],
                    "active": candidate["active"]
                })
                self.logger.info(f"💰 Dust found: {balance} {currency} = ${usd_value:.4f}")

        return dust_balances

    async def get_btc_account_uuid(self) -> Optional[str]:
        """Get the BTC account UUID from the accounts list."""
        accounts = await self.coinbase_api.get_accounts()

        for account in accounts:
            if account.get("currency") == "BTC":
                return account.get("uuid")

        self.logger.error("❌ BTC account not found!")
        return None

    async def convert_dust_to_btc(self, dust_balances: List[Dict]) -> Dict:
        """
        Convert dust balances to BTC using Coinbase Convert API.

        Args:
            dust_balances: List of dust balance dicts

        Returns:
            Summary dict with conversion results
        """
        if not dust_balances:
            self.logger.info("✅ No dust to convert")
            return {"converted": 0, "failed": 0, "total_usd": Decimal("0")}

        total_dust_usd = sum(d["usd_value"] for d in dust_balances)
        self.logger.info(f"\n💸 Total dust value: ${total_dust_usd:.4f}")

        if self.dry_run:
            self.logger.info("\n🔍 DRY RUN MODE - No actual conversions will be made")
            self.logger.info(f"Would convert {len(dust_balances)} dust balances:")
            for dust in dust_balances:
                self.logger.info(
                    f"  • {dust['balance']} {dust['currency']} "
                    f"(${dust['usd_value']:.4f}) → BTC"
                )
            return {"converted": 0, "failed": 0, "total_usd": total_dust_usd, "dry_run": True}

        # Get BTC account UUID
        btc_account_uuid = await self.get_btc_account_uuid()
        if not btc_account_uuid:
            return {
                "converted": 0,
                "failed": len(dust_balances),
                "total_usd": total_dust_usd,
                "reason": "btc_account_not_found"
            }

        # Convert each dust balance
        converted = 0
        failed = 0
        conversion_details = []

        for dust in dust_balances:
            currency = dust["currency"]
            balance = dust["balance"]
            from_account_uuid = dust["account_uuid"]

            self.logger.info(f"\n🔄 Converting {balance} {currency} to BTC...")

            try:
                # Step 1: Create convert quote
                quote_response = await self.coinbase_api.create_convert_quote(
                    from_account=from_account_uuid,
                    to_account=btc_account_uuid,
                    amount=str(balance)
                )

                if not quote_response.get("success"):
                    self.logger.error(
                        f"❌ Failed to create quote for {currency}: "
                        f"{quote_response.get('error')}"
                    )
                    failed += 1
                    conversion_details.append({
                        "currency": currency,
                        "amount": balance,
                        "status": "quote_failed",
                        "error": quote_response.get("error")
                    })
                    continue

                # Extract trade ID from quote
                trade_data = quote_response.get("data", {}).get("trade", {})
                trade_id = trade_data.get("id")

                if not trade_id:
                    self.logger.error(f"❌ No trade ID in quote response for {currency}")
                    failed += 1
                    continue

                # Log quote details
                total = trade_data.get("total", {})
                btc_amount = total.get("value", "unknown")
                self.logger.info(
                    f"📊 Quote: {balance} {currency} → {btc_amount} BTC"
                    f" (Trade ID: {trade_id[:8]}...)"
                )

                # Step 2: Commit the trade
                commit_response = await self.coinbase_api.commit_convert_trade(trade_id)

                if not commit_response.get("success"):
                    self.logger.error(
                        f"❌ Failed to commit trade {trade_id}: "
                        f"{commit_response.get('error')}"
                    )
                    failed += 1
                    conversion_details.append({
                        "currency": currency,
                        "amount": balance,
                        "trade_id": trade_id,
                        "status": "commit_failed",
                        "error": commit_response.get("error")
                    })
                    continue

                # Success!
                commit_data = commit_response.get("data", {}).get("trade", {})
                final_status = commit_data.get("status", "UNKNOWN")

                self.logger.info(
                    f"✅ Converted {balance} {currency} → {btc_amount} BTC "
                    f"(Status: {final_status})"
                )

                converted += 1
                conversion_details.append({
                    "currency": currency,
                    "amount": balance,
                    "btc_amount": btc_amount,
                    "trade_id": trade_id,
                    "status": "success"
                })

                # Small delay between conversions to avoid rate limits
                await asyncio.sleep(0.5)

            except Exception as e:
                self.logger.error(f"❌ Error converting {currency}: {e}", exc_info=True)
                failed += 1
                conversion_details.append({
                    "currency": currency,
                    "amount": balance,
                    "status": "exception",
                    "error": str(e)
                })

        return {
            "converted": converted,
            "failed": failed,
            "total_usd": total_dust_usd,
            "details": conversion_details
        }

    async def run(self):
        """Main execution flow"""
        try:
            await self.initialize()

            mode = "DRY RUN" if self.dry_run else "LIVE"
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"🚀 Dust to BTC Converter - {mode} MODE")
            self.logger.info(f"{'='*60}\n")
            self.logger.info(f"Dust threshold: ${self.dust_threshold_usd}")
            self.logger.info(f"Target currency: {self.target_currency}")
            self.logger.info(f"Excluded currencies: {', '.join(sorted(self.excluded_currencies))}\n")

            # Find dust balances
            dust_balances = await self.find_dust_balances()

            if not dust_balances:
                self.logger.info("\n✅ No dust found - nothing to convert")
                return

            self.logger.info(f"\n📋 Found {len(dust_balances)} dust balances\n")

            # Convert to BTC
            result = await self.convert_dust_to_btc(dust_balances)

            # Summary
            self.logger.info(f"\n{'='*60}")
            self.logger.info("📊 CONVERSION SUMMARY")
            self.logger.info(f"{'='*60}")
            self.logger.info(f"Total dust balances: {len(dust_balances)}")
            self.logger.info(f"Total dust value: ${result['total_usd']:.4f}")
            self.logger.info(f"Converted: {result['converted']}")
            self.logger.info(f"Failed: {result['failed']}")
            if "reason" in result:
                self.logger.info(f"Reason: {result['reason']}")
            self.logger.info(f"{'='*60}\n")

        except Exception as e:
            self.logger.error(f"❌ Error in dust converter: {e}", exc_info=True)
            raise

        finally:
            await self.cleanup()


async def main():
    """Entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Convert cryptocurrency dust to BTC")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be converted without executing")
    args = parser.parse_args()

    converter = DustConverter(dry_run=args.dry_run)
    await converter.run()


if __name__ == "__main__":
    asyncio.run(main())
