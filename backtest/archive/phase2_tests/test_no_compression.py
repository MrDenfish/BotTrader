from config_4h_hybrid import get_phase2_2_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine

config = get_phase2_2_baseline_config()
config.use_compression_filter = False  # DISABLE compression filter
config.vol_min_mult = 0.5  # Loose volatility filter

engine = Hybrid4hBacktestEngine(config, data_dir="data")
stats = engine.run_backtest("BTC-USD", 60)

print("\n" + "=" * 80)
print("TEST: COMPRESSION FILTER DISABLED")
print("=" * 80)
engine.print_results(stats, "BTC-USD")
print(f"\nTrades/month: {(stats['trades']/60)*30:.1f}")
