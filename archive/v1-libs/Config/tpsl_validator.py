"""
TP/SL Configuration Validator (Task 6)

Validates that TP/SL configuration is aligned across all 3 stop loss systems.
Ensures bracket orders, position monitor, and ATR-based entry stops coordinate properly.

Logs warnings if misalignment detected to prevent the 20% win rate issue.

Reference: docs/TPSL_COORDINATION_IMPLEMENTATION_PLAN.md Task 6
"""

import os
import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


def validate_tpsl_alignment() -> bool:
    """
    Validate that TP/SL configuration is aligned across all 3 systems.

    Checks:
    1. MAX_LOSS_PCT should match ATR-based stop calculation (within 0.5%)
    2. HARD_STOP_PCT should be wider than MAX_LOSS_PCT (emergency fallback)
    3. HARD_STOP_PCT gap should be reasonable (1-2% wider, not too far)
    4. MIN_PROFIT_PCT reasonable relative to MAX_LOSS_PCT (R:R ratio)

    Returns:
        True if configuration is aligned, False if issues detected
    """

    try:
        # Read configuration from environment
        atr_multiplier = float(os.getenv('ATR_MULTIPLIER_STOP', '1.8'))
        stop_min_pct = float(os.getenv('STOP_MIN_PCT', '0.012'))
        spread_cushion = float(os.getenv('SPREAD_CUSHION_PCT', '0.0015'))
        taker_fee = float(os.getenv('TAKER_FEE', '0.0055'))

        max_loss_pct = float(os.getenv('MAX_LOSS_PCT', '0.025'))
        min_profit_pct = float(os.getenv('MIN_PROFIT_PCT', '0.035'))
        hard_stop_pct = float(os.getenv('HARD_STOP_PCT', '0.05'))

        # Estimate typical ATR stop (assume ATR ~2% for typical crypto)
        typical_atr = 0.02
        estimated_atr_stop = max(atr_multiplier * typical_atr, stop_min_pct)

        # Add typical cushions (spread + fee)
        estimated_atr_stop += spread_cushion + taker_fee

        issues = []

        # Check 1: MAX_LOSS_PCT should match ATR stop (within 0.5%)
        if abs(max_loss_pct - estimated_atr_stop) > 0.005:
            issues.append(
                f"⚠️  MAX_LOSS_PCT ({max_loss_pct:.2%}) doesn't match estimated ATR stop "
                f"({estimated_atr_stop:.2%}). This will cause coordination conflicts!\n"
                f"    Recommended: MAX_LOSS_PCT={estimated_atr_stop:.3f} (set to {estimated_atr_stop*100:.1f}%)"
            )

        # Check 2: HARD_STOP should be wider than MAX_LOSS
        if hard_stop_pct <= max_loss_pct:
            issues.append(
                f"⚠️  HARD_STOP_PCT ({hard_stop_pct:.2%}) should be wider than "
                f"MAX_LOSS_PCT ({max_loss_pct:.2%}) to act as emergency fallback"
            )

        # Check 3: HARD_STOP gap should be reasonable (1-2% wider)
        gap = hard_stop_pct - max_loss_pct
        if gap > 0.025:
            issues.append(
                f"⚠️  HARD_STOP_PCT gap is large ({gap:.2%}). Consider tightening to "
                f"prevent large losses between SOFT and HARD stops.\n"
                f"    Recommended: HARD_STOP_PCT={max_loss_pct + 0.015:.3f} (1.5% wider)"
            )

        # Check 4: R:R ratio should be reasonable
        rr_ratio = min_profit_pct / max_loss_pct
        if rr_ratio < 0.5:
            issues.append(
                f"⚠️  Risk:Reward ratio is poor ({rr_ratio:.2f}). "
                f"MIN_PROFIT_PCT ({min_profit_pct:.2%}) should be at least 50% of "
                f"MAX_LOSS_PCT ({max_loss_pct:.2%}) for sustainable trading."
            )

        # Log results
        if issues:
            logger.warning("=" * 60)
            logger.warning("TP/SL CONFIGURATION ISSUES DETECTED:")
            logger.warning("")
            for issue in issues:
                logger.warning(issue)
            logger.warning("")
            logger.warning("Review docs/TPSL_CONFIGURATION_AUDIT.md for guidance")
            logger.warning("=" * 60)
            return False
        else:
            logger.info("=" * 60)
            logger.info("✅ TP/SL configuration validated - all systems aligned")
            logger.info(f"   MAX_LOSS_PCT: {max_loss_pct:.2%} (soft stop)")
            logger.info(f"   HARD_STOP_PCT: {hard_stop_pct:.2%} (emergency stop)")
            logger.info(f"   MIN_PROFIT_PCT: {min_profit_pct:.2%} (take profit)")
            logger.info(f"   Estimated ATR stop: {estimated_atr_stop:.2%}")
            logger.info(f"   R:R Ratio: {rr_ratio:.2f}")
            logger.info("=" * 60)
            return True

    except Exception as e:
        logger.error(f"Error validating TP/SL configuration: {e}", exc_info=True)
        return False


# Run validation on import (when module is loaded)
# This ensures validation happens at startup
if __name__ != "__main__":
    validate_tpsl_alignment()
