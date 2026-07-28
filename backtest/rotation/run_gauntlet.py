"""Gauntlet runner CLI.

Phase 1 (default): run PARAM_MENU on the fit era, pick the winner via the
spec section 12 floor rule (choose_fit_winner), report fit + validate for
that one config, and PIN the choice, era boundaries, and a cache
fingerprint to output/phase1_lock.json.

Phase 2 (--unlock-holdout): REQUIRE phase1_lock.json, recompute eras +
fingerprint from the current cache, and hard-exit if either drifted. Use
the LOCKED config (no re-sweep), run holdout ONCE, and print the final
verdict.

Results are written under backtest/rotation/output/ (gitignored — the
repo is public; numeric results never get committed).

Usage:
  python -m backtest.rotation.run_gauntlet --cache backtest/rotation/cache
  python -m backtest.rotation.run_gauntlet --cache ... --exposure 0.75
  python -m backtest.rotation.run_gauntlet --cache ... --unlock-holdout
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtest.rotation.data_store import DailyBarStore
from backtest.rotation.engine import RotationConfig
from backtest.rotation.walkforward import (
    PARAM_MENU,
    choose_fit_winner,
    era_bounds,
    passes_bar,
    run_walkforward,
)

BTC = "BTC-USD"
OUT = Path("backtest/rotation/output")
LOCK = OUT / "phase1_lock.json"


def _eras_iso(eras: dict) -> dict:
    """Serialize era boundary Timestamps to ISO strings for pinning/compare."""
    return {k: [start.isoformat(), end.isoformat()] for k, (start, end) in eras.items()}


def _fingerprint(bars: dict, exposure: float) -> dict:
    """Cache fingerprint: symbol count, BTC last-bar date, and exposure."""
    return {
        "n_symbols": len(bars),
        "btc_last_bar": bars[BTC].index[-1].isoformat(),
        "exposure": exposure,
    }


def _persist_equity(results: dict) -> None:
    for era, res in results.items():
        res.equity.to_csv(OUT / f"equity_{era}.csv", index_label="date", header=["equity"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--unlock-holdout", action="store_true")
    ap.add_argument("--exposure", type=float, default=1.0,
                    help="Gross exposure applied to target weights (default 1.0).")
    args = ap.parse_args()

    store = DailyBarStore(args.cache)
    bars = {s: store.load(s) for s in store.symbols()}
    bars = {s: df for s, df in bars.items() if df is not None and len(df) >= 200}
    if BTC not in bars:
        raise SystemExit(f"{BTC} missing from cache — it anchors the regime gate")

    eras = era_bounds(bars[BTC].index)
    OUT.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint(bars, args.exposure)

    if args.unlock_holdout:
        # -- Phase 2: locked config only, cache must be unchanged ------------
        if not LOCK.exists():
            raise SystemExit(
                f"Phase 1 lock {LOCK} not found — run Phase 1 (without "
                "--unlock-holdout) before unlocking the holdout."
            )
        lock = json.loads(LOCK.read_text())
        if lock["eras"] != _eras_iso(eras) or lock["fingerprint"] != fingerprint:
            raise SystemExit(
                "cache changed since Phase 1 — re-run Phase 1 or restore the "
                "cache. (era boundaries or fingerprint drifted; the holdout "
                "must be evaluated against the exact Phase 1 conditions)."
            )
        chosen = RotationConfig(**lock["config"])
        exposure = chosen.exposure
        print(f"Phase 2: using LOCKED config {lock['config']}  exposure {exposure:g}")

        phases = {"fit": eras["fit"], "validate": eras["validate"],
                  "holdout": eras["holdout"]}
        results = run_walkforward(bars, BTC, chosen, phases)
        _persist_equity(results)
        for era, res in results.items():
            print(f"{era:>9}: net {res.net_return:+.2%}  maxDD {res.max_drawdown:.2%}  "
                  f"trades {res.n_trades}  in-market {res.days_in_market}/{res.days_total}"
                  f"  exposure {exposure:g}")
        (OUT / "gauntlet_result.json").write_text(json.dumps(
            {era: {"net": r.net_return, "dd": r.max_drawdown, "trades": r.n_trades}
             for era, r in results.items()}, indent=2))

        verdict = passes_bar(results)
        print(f"VERDICT: {'PASS' if verdict['pass'] else 'FAIL'}")
        for r in verdict["reasons"]:
            print(f"  - {r}")
        print("NOTE: spec criterion 4 (regime gate avoids major bear legs) is a "
              "manual check — inspect equity_{era}.csv against BTC bear periods "
              "before treating PASS as final.")
        return

    # -- Phase 1: fit-era sweep, floor-rule winner, pin the choice -----------
    fit_rows = []
    for cfg in PARAM_MENU:
        res = run_walkforward(bars, BTC, cfg, {"fit": eras["fit"]})["fit"]
        fit_rows.append({"cfg": vars(cfg), "net": res.net_return,
                         "dd": res.max_drawdown, "trades": res.n_trades})
    fit_rows.sort(key=lambda r: r["net"], reverse=True)
    (OUT / "gauntlet_fit.json").write_text(json.dumps(fit_rows, indent=2, default=str))

    best = choose_fit_winner(fit_rows)
    if best is None:
        print("VERDICT: FAIL — no config met the drawdown cap on the fit era")
        return
    print(f"fit-era winner: {best['cfg']}  (details in {OUT}/gauntlet_fit.json)")

    chosen = RotationConfig(**best["cfg"])
    chosen.exposure = args.exposure  # F5: exposure applies to the chosen config

    # Pin config + eras + fingerprint so Phase 2 evaluates identical conditions.
    LOCK.write_text(json.dumps({
        "config": vars(chosen),
        "eras": _eras_iso(eras),
        "fingerprint": fingerprint,
    }, indent=2, default=str))

    phases = {"fit": eras["fit"], "validate": eras["validate"]}
    results = run_walkforward(bars, BTC, chosen, phases)
    _persist_equity(results)
    for era, res in results.items():
        print(f"{era:>9}: net {res.net_return:+.2%}  maxDD {res.max_drawdown:.2%}  "
              f"trades {res.n_trades}  in-market {res.days_in_market}/{res.days_total}"
              f"  exposure {args.exposure:g}")
    (OUT / "gauntlet_result.json").write_text(json.dumps(
        {era: {"net": r.net_return, "dd": r.max_drawdown, "trades": r.n_trades}
         for era, r in results.items()}, indent=2))

    print(f"(holdout locked — Phase 1 pinned to {LOCK}; rerun with "
          "--unlock-holdout for the final, one-time verdict)")


if __name__ == "__main__":
    main()
