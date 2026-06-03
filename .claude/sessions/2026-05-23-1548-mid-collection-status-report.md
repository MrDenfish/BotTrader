# Session: Mid-collection status report

**Date:** 2026-05-23
**Start time:** 15:48 AKDT
**Branch:** main
**Status:** In progress

---

## Session overview

The bot has been collecting live paper-trading data on AWS Kraken since 2026-04-12 (target review: July 2026). We're roughly 6 weeks into the ~12-week window. User wants a mid-collection status report on what's been accumulated so far — without taking any actions that would contaminate the experimental regime.

## Goals

- Pull current live data from AWS Postgres (v2_fills, v2_orders, FIFO round-trips).
- Summarize: trade count, win rate, gross/net P&L, exit-reason mix, hard-stop rate vs backtest baseline, symbol concentration.
- Compare against backtest expectations (PF 0.73, hard-stop ~22%, WR ~30-60%).
- Flag anything *observational* — but make no parameter changes (no-changes policy until July).

## Progress

- [x] AWS reachable; data fresh through 2026-05-23
- [x] Trade-count and date-range: 95 buys / 94 sells / 34 symbols since 2026-04-12 (41 of 120 target days, 34%)
- [x] P&L (from sell metadata, FIFO-accurate): net -$84.51, gross -$36.73, fees ~$48
- [x] Win rate 59.6% (56W / 38L), profit factor 0.59
- [x] Exit mix: trailing 62.8%, **hard_stop 34.0%** (vs 22% backtest baseline), stale 3.2%
- [x] RAVE-USD still dominant: 34/94 sells = 36% (down from 76% pre-collection per memory note)
- [x] Status report delivered to user

## Key metrics snapshot (2026-04-12 → 2026-05-23)

| Metric | Live | Backtest baseline | Direction |
|--------|------|-------------------|-----------|
| Win rate | 59.6% | 54-65% (sets A/B/C) | In line |
| Profit factor | 0.59 | 0.57-0.85 | Low end |
| Hard stop rate | 34.0% | ~22% | **Elevated** |
| Hard stop avg loss | -8.72% | -4.34% | **Wider** |
| Trailing stop avg gain | +2.46% | +1.69% | Better |
| Avg net/trade | -0.90 USD | -0.60 USD | Slightly worse |

## Open questions for July review
- Why is hard-stop avg loss almost 2× backtest? (-8.7% vs -4.34%) — possible ATR-stop calibration on micro-caps
- Will hard-stop rate normalize as RAVE concentration decays further?
- Last week (n=6) shows HS rate of 16.7%, WR 66.7% — too small to read as a trend, but worth watching

## Entry-pattern deep-dive findings (added 2026-05-23)

**Headline:** A single indicator-confirmation pattern explains most of the hard-stop excess.

### The pattern
80% of live trades (75 of 94) are "momentum-only" profile: Buy Ratio + Buy ROC + Buy Swing fire together, but **no** confirmation from the slower indicators (MACD, W-Bottom, RSI).

| Profile | n | HS% | Net P&L | Avg P&L |
|---------|---|-----|---------|---------|
| **No confirmation** (Swing/ROC/Ratio only) | 75 | **40.0%** | **-$98.33** | -1.93% |
| **With confirmation** (MACD or W-Bot or RSI ALSO fired) | 19 | **10.5%** | **+$13.82** | +0.27% |

- 30 of 32 hard stops (94%) came from the no-confirmation profile.
- The "with confirmation" bucket's 10.5% HS rate is close to backtest baseline (22%).
- 14 of 19 confirmation trades had MACD firing — MACD is the single most discriminating indicator.

### Why the existing trend gate doesn't catch this
The `min_trend_indicators=1` gate is satisfied by ROC OR Swing (both in the momentum trio). So a buy can pass the gate with the same 3-indicator pattern that produces 40% hard stops. **The gate counts presence, not diversification.**

### Symbol pattern (separate axis)
Three symbols are categorically broken in the live window:
- **INJ-USD**: 3 trades, 3 hard stops (100%), -$17 P&L
- **PLAY-USD**: 3 trades, 3 hard stops (100%), -$17 P&L
- **AI-USD**: 4 trades, 3 hard stops (75%), -$16 P&L

These 10 trades = 9 of 32 hard stops (28%). PerformanceFilter would catch this after 30 days at -$50; INJ and AI are now below that threshold. Worth verifying the filter is actually excluding them.

### Counterfactual (small-sample caveat applies)
If the live bot had required confirmation:
- 75 trades skipped, 19 kept
- Estimated net P&L: +$14 instead of -$85 (a ~$99 swing, 19 trades)
- 95% CI on the 10.5% HS rate is wide (≈ 1.3% to 33%) due to small n=19 — the point estimate could be 25% in reality

### Other entry features
- RVOL: 1.2-1.6 sweet spot (HS 21%) but the relationship is non-monotonic — noise
- ADX: identical means across exit types (32.0 vs 32.7) — not predictive
- ATR percentile: identical means (34.8 vs 31.4) — not predictive
- Score magnitude: identical means (5.45 vs 5.34) — not predictive
- Hold time: hard stops take 1.5× longer than trailing wins (390 vs 252 min avg) — slow grind down, not flash crashes
- Time of day: 00-06 UTC has 47% HS vs 23% in 06-12 UTC — suggestive but small samples

### Policy reminder
No-changes-until-July. This is observational evidence for the July review, not a directive. Key new question for July: does a `min_deep_indicators=1` (require ≥1 of MACD/W-Bottom/RSI) gate hold up over 60+ more days?

---

## SESSION END SUMMARY (2026-05-23 16:09 AKDT)

**Duration:** ~21 minutes (15:48 → 16:09 AKDT)
**Branch:** main (no commits made — pure analysis session)

### Git summary
- **Repo commits made:** 0 — this was a read-only / memory-only session
- **Tracked files changed in working tree:** 0 from this session
  - `.claude/sessions/.current-session` (typechange) — pre-existing from prior session
  - `.idea/BotTrader.iml` (modified) — pre-existing IDE noise
- **Untracked files (new):**
  - `.claude/sessions/2026-05-23-1548-mid-collection-status-report.md` (this file, created this session)
  - Two prior session files (`2026-05-12-...`, `2026-05-16-...`) were already untracked before this session

### Memory files updated (user-private, not in repo)
- `~/.claude/projects/.../memory/july_2026_evaluation_prep.md` — appended "Mid-collection findings (2026-05-23, n=94 round trips)" section with the indicator-confirmation hypothesis, per-indicator hit rate table, caveats, and a 4-step July action sequence.
- `~/.claude/projects/.../memory/MEMORY.md` — added one-sentence pointer to the P1 line so future sessions land on the new section.

### Todo summary
No TaskCreate items were used this session; the working plan stayed inline in the session file's "Progress" checklist (all 6 items completed):
- [x] Confirm AWS connectivity and freshness of data
- [x] Pull trade-count and date-range summary
- [x] Pull FIFO round-trip P&L summary
- [x] Pull exit-reason breakdown
- [x] Pull per-symbol P&L and concentration
- [x] Write status report
- [x] (Stretch) Entry-pattern deep-dive after user's ultrathink follow-up

### Key accomplishments
1. **Mid-collection status report** delivered to user. 41 of ~120 target days complete (34%). Headline: PF 0.59, WR 59.6%, net P&L -$84.51, **hard stop rate 34% vs 22% backtest baseline**, hard stop avg loss -8.72% vs -4.34% backtest (~2× wider).
2. **Entry-pattern deep-dive** identified one concrete falsifiable hypothesis: 80% of trades fire only the "momentum trio" (Buy Ratio + Buy ROC + Buy Swing) with no slower confirmation; this profile has 40% HS rate vs 10.5% when MACD/W-Bottom/RSI also fires. 94% of all hard stops came from no-confirmation profile.
3. **Symbol-level finding**: INJ-USD, PLAY-USD, AI-USD account for 28% of all hard stops in 10 trades (75-100% HS rate each). PerformanceFilter activation status to verify in July.
4. **Memory updated** with the findings so the July review starts here, not from scratch.

### Notable findings / breaking insights
- The existing `min_trend_indicators=1` gate counts presence, not depth across indicator families. ROC and Swing are both in the momentum trio, so the gate is effectively always satisfied without forcing a slower confirmation. This is likely why the April backtest didn't see this — backtest universe (BTC/ETH/SOL etc.) probably has cleaner momentum than the live universe (heavy on micro-caps like RAVE/PLAY/INJ).
- The April fee/sizing conclusion ("entry filters don't discriminate") was true for scalar filters (ADX, RVOL, regime). It is NOT true for indicator-family composition. The discriminator is *which* indicators fire, not how many or how strong the macro context is.
- Hard stops take 1.5× longer to develop than trailing wins (390 min avg vs 252 min). Slow grinds, not flash crashes — argues against blaming low-liquidity hours.

### Problems encountered & solutions
- **Postgres `ROUND(double precision, int)` error**: First aggregate query failed because P&L math returns `double precision`. Fixed by casting expressions to `numeric` before `ROUND`. Pattern reused throughout the session.
- **Large `jsonb_pretty` output (74KB) hit tool-output cap**: Streamed to a persisted file and Read'd back. Useful to know for future schema inspection on rich JSON fields.

### Deployment steps taken
None. AWS commit unchanged at `0fc778d`; the docs-only `bf0ac03` correctly remains undeployed (no need).

### Configuration changes
None. The no-parameter-changes policy held — this session was purely analytical.

### Lessons for future developers / AIs
1. **The FIFO pairing pattern works:** `ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp)` separately on buys and sells, then `JOIN USING (symbol, rn)`. Validated again this session on 94 round trips. Do NOT filter buys/sells by date before pairing — that breaks the rn alignment.
2. **Buy fill metadata is rich enough for entry-pattern analysis without round-trip diagnostic JSONL.** `metadata->'indicator_snapshot'->'<Indicator>'->>'decision'` gives the per-indicator fire/no-fire decision. `metadata->>'trigger'`, `'buy_score'`, `'adx'`, `'rvol'`, `'atr_percentile'` are all available. Use this pattern for future ad-hoc analyses.
3. **Means can collapse while distributions diverge.** ADX, score, RVOL means were nearly identical for HS vs winners, but the indicator-profile axis exposed a 40% vs 10.5% gap. Always check categorical/multi-feature pivots before concluding "no signal."
4. **Don't generalize backtest filter findings to live data without re-testing.** The backtest universe and live universe are structurally different (mainstream alts vs micro-caps).
5. **Small-sample caveats matter.** The 19-trade "with confirmation" bucket has a HS rate 95% CI of ~1.3% to 33%. The headline point estimate (10.5%) is suggestive, not conclusive. Apply OOS discipline before any deploy.
6. **Use Read for persisted tool-output files.** When `psql -c "SELECT jsonb_pretty(...)"` exceeds the output cap, the harness saves it to a file path — Read with `limit=200` to inspect schema without re-running.

### What wasn't completed (deliberately deferred)
- Verification that PerformanceFilter is actually excluding INJ/AI/PLAY in production (both INJ at -$17 and AI at -$16 are short of the -$50 trigger; -$50 threshold checks total over 30d). Flagged as a July check.
- Counterfactual backtest of `min_deep_indicators=1` on OOS sets A/B/C. Deferred to July per policy.
- Per-RVOL-bucket × indicator-profile interaction analysis. Noted as candidate for July deep-dive.

### Tips for the next session
- The mid-collection findings are persisted in `july_2026_evaluation_prep.md` under "Mid-collection findings (2026-05-23, n=94 round trips)". Read that section first before redoing queries.
- AWS is at `0fc778d`. The docs commit `bf0ac03` is local-main-only and correctly undeployed.
- Three untracked session files in `.claude/sessions/` are normal — session files are user-private; the repo doesn't track them.
- If the user asks for another mid-window snapshot before July, the SQL patterns in this file are reusable. The pairing CTE and the indicator-profile CASE expression are the load-bearing pieces.
