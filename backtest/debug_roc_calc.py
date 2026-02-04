"""Quick diagnostic to check ROC-score calculation"""

import pandas as pd
import numpy as np
from pathlib import Path
from config_4h_hybrid import get_phase2_3_roc_baseline_config
from engine_4h_hybrid import Hybrid4hBacktestEngine

# Load config
config = get_phase2_3_roc_baseline_config()
engine = Hybrid4hBacktestEngine(config)

# Load 1m data
df_1m = engine.load_data("BTC-USD", days=180)
print(f"Loaded {len(df_1m)} 1m bars")
print(f"Date range: {df_1m.index[0]} to {df_1m.index[-1]}")
print()

# Calculate indicators
df_4h, df_1d, df_aligned = engine.calculate_indicators(df_1m)
print(f"4h bars: {len(df_4h)}")
print(f"1D bars: {len(df_1d)}")
print()

# Check 4h dataframe columns
print("4h DataFrame columns:")
print(df_4h.columns.tolist())
print()

# Check if roc_score_4h exists and has values
if 'roc_score_4h' in df_4h.columns:
    print("roc_score_4h column found in df_4h")
    print(f"  Min: {df_4h['roc_score_4h'].min()}")
    print(f"  Max: {df_4h['roc_score_4h'].max()}")
    print(f"  Mean: {df_4h['roc_score_4h'].mean()}")
    print(f"  Non-zero count: {(df_4h['roc_score_4h'] != 0).sum()}")
    print(f"  NaN count: {df_4h['roc_score_4h'].isna().sum()}")
    print()
    print("First 10 values:")
    print(df_4h[['close', 'atr_pct', 'roc_4h', 'roc_score_4h']].head(20))
else:
    print("❌ roc_score_4h NOT FOUND in df_4h")
    print()

# Check aligned dataframe
if 'roc_score_4h' in df_aligned.columns:
    print("\nroc_score_4h column found in df_aligned")
    print(f"  Min: {df_aligned['roc_score_4h'].min()}")
    print(f"  Max: {df_aligned['roc_score_4h'].max()}")
    print(f"  Mean: {df_aligned['roc_score_4h'].mean()}")
    print(f"  Non-zero count: {(df_aligned['roc_score_4h'] != 0).sum()}")
    print(f"  NaN count: {df_aligned['roc_score_4h'].isna().sum()}")
else:
    print("❌ roc_score_4h NOT FOUND in df_aligned")
