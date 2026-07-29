"""Regime-gated carry gauntlet (spec 2026-07-28, sections 7-9).

Single-phase: calibrate exposure per config on the fit era, run fit +
validate, pick the winner, print the verdict against the pre-registered
pass bar. There is NO holdout phase — forward paper trading is the
holdout. Outputs land in backtest/rotation/output/ (gitignored; the
repo is public — never commit numeric results).

Usage:
  conda run -n tradebot python -m backtest.rotation.run_carry_gauntlet \
      --cache backtest/rotation/cache
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path

from backtest.rotation.carry_engine import CarryBacktest, CarryConfig
from backtest.rotation.carry_walkforward import (
    CARRY_MENU,
    calibrate_exposure,
    carry_eras,
)
from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.walkforward import MAX_DD, passes_bar

OUT = Path("backtest/rotation/output")


def pick_winner(rows: list[dict]) -> dict | None:
    eligible = [r for r in rows if r["fit"].max_drawdown <= MAX_DD]
    if not eligible:
        return None
    return max(eligible, key=lambda r: r["fit"].net_return)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    args = ap.parse_args()

    store = DailyBarStore(args.cache)
    universe = CARRY_MENU[0].universe
    btc = CARRY_MENU[0].btc
    bars = {}
    for sym in set(universe) | {btc}:
        df = store.load(sym)
        if df is None:
            raise SystemExit(f"{sym} missing from cache")
        bars[sym] = df

    eras = carry_eras(bars[btc].index)
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for cfg in CARRY_MENU:
        expo, fit_res = calibrate_exposure(bars, cfg, eras["fit"])
        cfg_run = dataclasses.replace(cfg, exposure=expo)
        val_res = CarryBacktest(bars, cfg_run).run(*eras["validate"])
        rows.append({"cfg": cfg_run, "exposure": expo,
                     "fit": fit_res, "validate": val_res})
        print(f"{cfg.scheme:>12}: exposure {expo:.3f}  "
              f"fit net {fit_res.net_return:+.2%} DD {fit_res.max_drawdown:.2%}  "
              f"validate net {val_res.net_return:+.2%} DD {val_res.max_drawdown:.2%}")
        fit_res.equity.to_csv(OUT / f"carry_equity_fit_{cfg.scheme}.csv")
        val_res.equity.to_csv(OUT / f"carry_equity_validate_{cfg.scheme}.csv")

    winner = pick_winner(rows)
    if winner is None:
        print("VERDICT: FAIL — no config met the fit-era drawdown cap")
        return
    era_results = {"fit": winner["fit"], "validate": winner["validate"]}
    verdict = passes_bar(era_results)
    print(f"winner: {winner['cfg'].scheme}  exposure {winner['exposure']:.3f}")
    print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'}")
    for r in verdict["reasons"]:
        print(f"  - {r}")
    print("NOTE: spec criterion 3 (2018 + 2022 bear-leg avoidance) is a manual "
          "check — inspect carry_equity_fit_*.csv before treating PASS as final.")
    (OUT / "carry_result.json").write_text(json.dumps({
        "winner": winner["cfg"].scheme, "exposure": winner["exposure"],
        "fit": {"net": winner["fit"].net_return, "dd": winner["fit"].max_drawdown,
                "trades": winner["fit"].n_trades},
        "validate": {"net": winner["validate"].net_return,
                     "dd": winner["validate"].max_drawdown,
                     "trades": winner["validate"].n_trades},
        "pass": verdict["pass"], "reasons": verdict["reasons"],
    }, indent=2))


if __name__ == "__main__":
    main()
