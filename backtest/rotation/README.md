# Rotation Backtest — Data Sourcing & Gauntlet Usage

Validation harness for the momentum rotation strategy
(spec: `docs/superpowers/specs/2026-07-27-momentum-rotation-design.md`).

## Data sourcing

Signals run on daily bars. Kraken's REST OHLC endpoint returns at most
~720 daily bars, so multi-year history must come from Kraken's bulk
OHLCVT export (support.kraken.com → "Downloadable historical OHLCVT
data"): a full-history `Kraken_OHLCVT.zip` (~7 GB) plus quarterly
incremental zips, hosted on Google Drive.

1. Download the zip(s) into `backtest/rotation/cache/_bulk/` (gitignored).
   The Drive links enforce a shared daily download quota; if every file
   returns "Quota exceeded", wait ~24h and retry, or (logged into Google)
   use *Make a copy* on the file and download your own copy.
2. Import daily USD-pair bars and REST-top-up the recent gap
   (script pattern: extract `*USD_1440.csv` members, map pair names via
   `v2.utils.symbol_mapper` — XBT→BTC, XDG→DOGE — then
   `DailyBarStore.import_ohlcvt_csv(...)` followed by
   `top_up_from_rest(...)`). **Always import bulk files BEFORE the REST
   top-up for a symbol**: the cache keeps the first-seen row per date, so
   a later bulk import cannot repair dates already filled by REST.
3. Sanity-check: BTC-USD should have ≥ 1,800 bars, no gap > 3 days, last
   bar within 2 days of today; spot-check two closes against public charts.

## Running the gauntlet

Phase 1 — fit-era sweep over the closed parameter menu, winner chosen by
the pre-registered rules (drawdown cap, §12 volume-floor sensitivity rule),
then reported on fit + validate:

    conda run -n tradebot python -m backtest.rotation.run_gauntlet \
        --cache backtest/rotation/cache [--exposure 1.0]

Phase 1 writes `output/phase1_lock.json` (chosen config, era boundaries,
cache fingerprint) and per-era `output/equity_*.csv`.

Phase 2 — the one-time holdout verdict:

    conda run -n tradebot python -m backtest.rotation.run_gauntlet \
        --cache backtest/rotation/cache --unlock-holdout

**Rules:**
- **The cache is frozen between Phase 1 and Phase 2.** Phase 2 verifies a
  fingerprint and refuses to run if the cache changed — do not top up,
  re-import, or add symbols between phases. (The fingerprint keys on the
  BTC series and symbol count; don't rely on it catching every possible
  edit — just don't touch the cache.)
- Run Phase 2 **once**. Reviewing holdout results and then changing
  parameters is the exact overfitting failure this protocol exists to
  prevent.
- The printed PASS/FAIL covers 3 of the spec's 4 criteria; criterion 4
  (regime gate avoids major bear legs) is a manual check against the
  `equity_*.csv` curves — the runner prints a reminder.
- BTC-USD passes the universe screens and may legitimately be selected as
  a holding; it also anchors the regime gate and the trading calendar.

## Carry gauntlet

Single-phase runner for the regime-gated carry strategy
(spec `docs/superpowers/specs/2026-07-28-regime-gated-carry-design.md`):

    conda run -n tradebot python -m backtest.rotation.run_carry_gauntlet \
        --cache backtest/rotation/cache

Eras are fixed dates (fit 2017-01-01 → 2025-01-25; validate 2025-01-26 →
last cached bar). Exposure is bisection-calibrated per config on the fit
era to a 12–14% max-DD window. There is NO holdout phase — forward paper
trading is the holdout. Bear-leg avoidance (2018, 2022) is a manual check
against `output/carry_equity_fit_*.csv`. Outputs are gitignored; never
commit numeric results.

## Public-repo rule

This repository is public. `output/` and `cache/` are gitignored; never
commit numeric backtest results, equity curves, or chosen-parameter
discussion. Result summaries belong in the private project notes.
