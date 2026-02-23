"""Grid search optimizer for v2 composite scoring strategy.

Runs the full v2 backtest pipeline over parameter combinations and
ranks results by configurable objective (default: net P&L).

Usage:
    python -m v2 optimize --config v2/backtest_composite.yaml \\
                          --grid v2/backtest/grids/composite_v1.py

Grid files define a GRID dict mapping dotted config paths to value lists:
    GRID = {
        "strategies.0.config.score_buy_target": [1.5, 2.0, 2.5],
        "risk.1.hard_stop_pct": [0.03, 0.045, 0.06],
    }
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import importlib.util
import itertools
import logging
import sys
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def _set_nested(d: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using dot notation.

    Supports integer keys for list indexing:
        "strategies.0.config.score_buy_target" sets d["strategies"][0]["config"]["score_buy_target"]
    """
    keys = dotted_key.split(".")
    for k in keys[:-1]:
        if k.isdigit():
            d = d[int(k)]
        else:
            d = d[k]
    final = keys[-1]
    if final.isdigit():
        d[int(final)] = value
    else:
        d[final] = value


def _load_grid(grid_path: str) -> dict[str, list]:
    """Load a GRID dict from a Python file."""
    spec = importlib.util.spec_from_file_location("grid_module", grid_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "GRID"):
        raise ValueError(f"Grid file {grid_path} must define a GRID dict")
    return mod.GRID


def run_single(base_config: dict, overrides: dict[str, Any]) -> dict:
    """Run a single backtest with parameter overrides, return results dict."""
    from v2.core.app import App

    cfg = copy.deepcopy(base_config)
    for key, value in overrides.items():
        _set_nested(cfg, key, value)

    app = App(overrides=cfg)
    result = asyncio.run(app.run())
    return result or {}


def optimize(
    base_config: dict,
    grid: dict[str, list],
    output_csv: str | None = None,
    top_n: int = 10,
) -> list[dict]:
    """Run grid search over parameter combinations.

    Returns list of result dicts sorted by net_pnl descending.
    """
    param_names = list(grid.keys())
    param_values = list(grid.values())
    combinations = list(itertools.product(*param_values))
    total = len(combinations)

    print("=" * 70)
    print("V2 COMPOSITE SCORING — GRID SEARCH OPTIMIZER")
    print("=" * 70)
    print(f"  Symbols: {base_config.get('app', {}).get('symbols', [])}")
    print(f"  Parameters: {len(param_names)}")
    print(f"  Combinations: {total}")
    for name in param_names:
        print(f"    {name}: {grid[name]}")
    print("=" * 70)
    print()

    results = []
    t0 = time.time()

    for idx, values in enumerate(combinations, 1):
        overrides = dict(zip(param_names, values))
        short = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in overrides.items())
        print(f"[{idx}/{total}] {short}", end=" ... ", flush=True)

        try:
            t1 = time.time()
            # Suppress logging during optimization
            logging.getLogger("v2").setLevel(logging.CRITICAL)
            stats = run_single(base_config, overrides)
            logging.getLogger("v2").setLevel(logging.WARNING)
            elapsed = time.time() - t1

            row = {
                **overrides,
                "trades": stats.get("trades", 0),
                "winners": stats.get("winners", 0),
                "losers": stats.get("losers", 0),
                "net_pnl": round(stats.get("net_pnl", 0), 2),
                "gross_pnl": round(stats.get("gross_pnl", 0), 2),
                "fees": round(stats.get("fees", 0), 2),
                "win_rate": round(stats.get("win_rate", 0), 4),
                "profit_factor": round(stats.get("profit_factor", 0), 2),
                "max_drawdown_pct": round(stats.get("max_drawdown_pct", 0), 2),
                "avg_hold_min": round(stats.get("avg_hold_minutes", 0), 0),
                "buy_fills": stats.get("buy_fills", 0),
                "sell_fills": stats.get("sell_fills", 0),
                "vetoes": stats.get("vetoes", 0),
                "exit_reasons": str(stats.get("exit_reasons", {})),
                "elapsed_sec": round(elapsed, 1),
            }
            results.append(row)
            print(f"{row['trades']}T {row['net_pnl']:+.2f} PnL {row['win_rate']:.0%}WR ({elapsed:.0f}s)")
        except Exception as e:
            logger.error("Failed: %s — %s", short, e)
            print(f"ERROR: {e}")

    # Sort by net_pnl descending
    results.sort(key=lambda r: r["net_pnl"], reverse=True)

    total_time = time.time() - t0
    print()
    print("=" * 70)
    print(f"OPTIMIZATION COMPLETE — {total} runs in {total_time:.0f}s")
    print("=" * 70)

    # Print top N
    print(f"\nTOP {min(top_n, len(results))} CONFIGURATIONS:")
    print("-" * 70)
    for i, row in enumerate(results[:top_n], 1):
        params = {k: row[k] for k in param_names}
        short = ", ".join(f"{k.split('.')[-1]}={v}" for k, v in params.items())
        print(
            f"  {i:>2}. PnL=${row['net_pnl']:>7.2f}  "
            f"WR={row['win_rate']:>5.1%}  "
            f"PF={row['profit_factor']:>5.2f}  "
            f"T={row['trades']:>3}  "
            f"DD={row['max_drawdown_pct']:.1f}%  "
            f"| {short}"
        )

    # Export to CSV
    if output_csv and results:
        with open(output_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults saved to: {output_csv}")

    return results


def optimize_main(argv: list[str]) -> None:
    """CLI entry point for optimizer."""
    parser = argparse.ArgumentParser(
        prog="bottrader-v2 optimize",
        description="Grid search optimizer for v2 composite scoring strategy",
    )
    parser.add_argument(
        "--config", "-c",
        required=True,
        help="Path to base YAML config file",
    )
    parser.add_argument(
        "--grid", "-g",
        required=True,
        help="Path to Python grid definition file (must define GRID dict)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output CSV file path (default: auto-named in current directory)",
    )
    parser.add_argument(
        "--top", "-n",
        type=int,
        default=10,
        help="Number of top results to display (default: 10)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )

    # Load base config
    with open(args.config) as f:
        base_config = yaml.safe_load(f)

    # Load grid
    grid = _load_grid(args.grid)

    # Default output path
    output = args.output
    if not output:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"optimizer_results_{ts}.csv"

    optimize(base_config, grid, output_csv=output, top_n=args.top)
