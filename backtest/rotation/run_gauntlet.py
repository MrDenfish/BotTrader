"""Gauntlet runner CLI.

Phase 1 (default): run PARAM_MENU on the fit era, pick the best config
(net return, subject to DD <= 15%), report fit + validate for that one
config. Phase 2 (--unlock-holdout): run the chosen config on holdout,
ONCE, and print the final verdict.

Results are written under backtest/rotation/output/ (gitignored — the
repo is public; numeric results never get committed).

Usage:
  python -m backtest.rotation.run_gauntlet --cache backtest/rotation/cache
  python -m backtest.rotation.run_gauntlet --cache ... --unlock-holdout
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.engine import RotationConfig
from backtest.rotation.walkforward import (
    MAX_DD,
    PARAM_MENU,
    era_bounds,
    passes_bar,
    run_walkforward,
)

BTC = "BTC-USD"
OUT = Path("backtest/rotation/output")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--unlock-holdout", action="store_true")
    args = ap.parse_args()

    store = DailyBarStore(args.cache)
    bars = {s: store.load(s) for s in store.symbols()}
    bars = {s: df for s, df in bars.items() if df is not None and len(df) >= 200}
    if BTC not in bars:
        raise SystemExit(f"{BTC} missing from cache — it anchors the regime gate")

    eras = era_bounds(bars[BTC].index)
    OUT.mkdir(parents=True, exist_ok=True)

    # Phase 1: fit-era sweep over the closed menu.
    fit_rows = []
    for cfg in PARAM_MENU:
        res = run_walkforward(bars, BTC, cfg, {"fit": eras["fit"]})["fit"]
        fit_rows.append({"cfg": vars(cfg), "net": res.net_return,
                         "dd": res.max_drawdown, "trades": res.n_trades})
    fit_rows.sort(key=lambda r: r["net"], reverse=True)
    eligible = [r for r in fit_rows if r["dd"] <= MAX_DD]
    if not eligible:
        print("VERDICT: FAIL — no config met the drawdown cap on the fit era")
        (OUT / "gauntlet_fit.json").write_text(json.dumps(fit_rows, indent=2, default=str))
        return
    best = eligible[0]
    print(f"fit-era winner: {best['cfg']}  (details in {OUT}/gauntlet_fit.json)")
    (OUT / "gauntlet_fit.json").write_text(json.dumps(fit_rows, indent=2, default=str))

    chosen = RotationConfig(**best["cfg"])

    phases = {"fit": eras["fit"], "validate": eras["validate"]}
    if args.unlock_holdout:
        phases["holdout"] = eras["holdout"]
    results = run_walkforward(bars, BTC, chosen, phases)
    for era, res in results.items():
        print(f"{era:>9}: net {res.net_return:+.2%}  maxDD {res.max_drawdown:.2%}  "
              f"trades {res.n_trades}  in-market {res.days_in_market}/{res.days_total}")
    (OUT / "gauntlet_result.json").write_text(json.dumps(
        {era: {"net": r.net_return, "dd": r.max_drawdown, "trades": r.n_trades}
         for era, r in results.items()}, indent=2))

    if args.unlock_holdout:
        verdict = passes_bar(results)
        print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'}")
        for r in verdict["reasons"]:
            print(f"  - {r}")
    else:
        print("(holdout locked — rerun with --unlock-holdout for the final, "
              "one-time verdict)")


if __name__ == "__main__":
    main()
