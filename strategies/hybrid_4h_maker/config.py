"""
Configuration re-export for the 4h Hybrid Maker Strategy plugin.

Re-exports Hybrid4hConfig from the backtest config module so that
users of the plugin package can import from a single location.
"""

from backtest.config_4h_hybrid import (
    Hybrid4hConfig,
    get_default_config,
    get_conservative_config,
    get_aggressive_config,
    get_closer_to_actual_fees_config,
    get_phase2_baseline_config,
    get_phase2_1_hardened_config,
    get_phase2_2_baseline_config,
    get_phase2_3_roc_baseline_config,
    get_phase2_4_stage_c_test_config,
)

__all__ = [
    'Hybrid4hConfig',
    'get_default_config',
    'get_conservative_config',
    'get_aggressive_config',
    'get_closer_to_actual_fees_config',
    'get_phase2_baseline_config',
    'get_phase2_1_hardened_config',
    'get_phase2_2_baseline_config',
    'get_phase2_3_roc_baseline_config',
    'get_phase2_4_stage_c_test_config',
]
