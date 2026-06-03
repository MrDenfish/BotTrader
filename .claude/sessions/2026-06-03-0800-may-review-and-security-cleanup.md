# Session: May Review & Security Cleanup — 2026-06-03

**Start:** ~08:00 (approximate; session never formally started via session-start)
**End:** ~11:40
**Duration:** ~3h 40m

---

## Overview

Three workstreams in one session:
1. **Project status + May 2026 monthly review.** Started as a status-recap request, expanded into the first formal monthly observational review (template + data + memory file).
2. **Security audit + remediation.** Triggered by deciding whether to commit the May review memory to the public repo. Discovered a Coinbase API private key and the production Postgres password were committed to a public GitHub repo. Rotated both credentials, scrubbed HEAD, hardened gitignore.
3. **Housekeeping.** Archived three stale untracked session files, untracked `.idea/`, updated SYSTEM_CONTEXT changelog, updated MEMORY.md.

Net: 5 commits, all pushed to `origin/main`. No code-behavior changes — all infra/security/docs.

---

## Git Summary

**Branch:** `main`
**Final HEAD:** `fcdb55a`
**Origin status:** up to date
**Commits made:** 5 (all pushed)

| Hash | Time | Message | Files | +/- |
|---|---|---|---|---|
| `f16e5c3` | 09:50 | chore(security): untrack leaked credentials and harden .gitignore | 3 | +8 / -385 |
| `9ad211a` | 10:03 | chore(security): scrub leaked Postgres password from HEAD | 10 | +13 / -12 |
| `9316eb4` | 10:17 | docs: update SYSTEM_CONTEXT for 2026-06-03 security cleanup | 1 | +2 / -1 |
| `c3738ce` | 10:24 | docs: archive May 2026 session notes (audit, bot health, mid-collection) | 3 | +550 / 0 |
| `fcdb55a` | 10:30 | chore: untrack .idea/ (already in .gitignore) | 10 | 0 / -165 |

**Files changed (consolidated, by category):**

*Untracked (removed from index, files remain on disk):*
- `Config/websocket_api_info.json` (Coinbase API private key — rotated, file removed)
- `.claude/testing/test_peak_tracking_edge.py` (hardcoded DB password)
- 10× `.idea/*` (PyCharm project files; `.idea/` already gitignored)

*Sanitized (literal old password replaced):*
- `backtest/download_historical_data.py` — `os.environ['DB_PASSWORD']`
- `scripts/check_backtest_data.py` — `os.environ['DB_PASSWORD']`
- 7× archive files (`archive/backtest_runners/*.py`, `archive/v1-libs/database/strategy_snapshot_manager.py`, `archive/v1-libs/scripts/diagnostics/verify_report_accuracy.py`, `backtest/archive/multi_roc_1m/*.py`) — `***REDACTED***`
- 1× session note (`.claude/sessions/2026-01-14-strategy-optimization-backtest.md`) — `***REDACTED***`

*Added:*
- `.claude/sessions/2026-05-12-project-audit-pass3-pass4.md` (197 lines)
- `.claude/sessions/2026-05-16-bot-health-and-sonnet-upgrade.md` (194 lines)
- `.claude/sessions/2026-05-23-1548-mid-collection-status-report.md` (159 lines)

*Modified:*
- `.gitignore` (added `Config/*_api_info.json`, `Config/*.key`, `Config/*.pem`, and `.claude/testing/test_peak_tracking_edge.py` lines)
- `docs/SYSTEM_CONTEXT.md` (changelog entry + Configuration section update)

**Final `git status`:** clean. Working tree matches HEAD; HEAD matches origin/main.

---

## Production Changes (AWS / EC2 / DB)

All on `bottrader-aws` (production):

1. **Postgres password rotated** via `ALTER USER bot_user PASSWORD '<24-char alphanumeric>';` inside the running `db` container.
2. **`/opt/bot/.env` updated** in-place via `sed -i.bak` (atomic line replacement of `DB_PASSWORD=`).
3. **`docker compose up -d --force-recreate v2-kraken dashboard`** triggered recreate of all four containers (compose detected env interpolation change). Data volume persisted; db came up healthy in ~30s.
4. **Verified:** v2-kraken logged `PostgreSQL connected (pool_size=5)`, hydrated 553 historical fills from DB, pair discovery active (30 USD pairs, avg volume $1.7M — recovered from May regime dip), WebSocket connected. Dashboard Streamlit started clean. No auth errors anywhere.
5. **Stale files removed from `/opt/bot`:**
   - `.env.bak` (created by the sed step)
   - `.env.backup-20260116-014036` (January snapshot, contained old password)
   - `.env.backup.20260127_113344` (January snapshot, contained old password)

Production left in operational state. No downtime visible to anything reading the dashboard; consumer reconnect was ~5–10s.

---

## Memory (`~/.claude/projects/-Users-Manny-Python-Projects-BotTrader/memory/`)

**New files:**
- `monthly_review_2026-05.md` (project type, 9.2KB) — first formal monthly review under the new template. Headline: PF 0.37 (worst since collection began), HS rate 29.3% in May / 34.3% cumulative, deep-indicator confirmation hypothesis replicated cleanly (8.3% HS vs 37.9% no-deep, ~4.5× discrimination), PLAY/INJ/AI account for 67% of May hard stops and are NOT being excluded by PerformanceFilter despite hitting 2/3 thresholds. Methodology gotcha logged: naive SQL `ROW_NUMBER` FIFO misalignment vs production matcher.
- `feedback_public_repo_discipline.md` (feedback type) — the going-forward rule that BotTrader repo is public on GitHub; never commit credentials, P&L analysis, or trading-edge hypotheses; analytical memory stays in `~/.claude/projects/.../memory/` only.

**MEMORY.md updates:**
- Critical Caveats: added top-line reminder that the repo is PUBLIC with backlink to `feedback-public-repo-discipline`.
- Project Structure / `Config/`: updated to reflect `websocket_api_info.json` removal and broader gitignore globs.
- Operational Events: full 2026-06-03 security cleanup entry with commit hashes.
- Monthly Reviews: new section pointing to `monthly_review_2026-05.md`.
- Open Work Items: split P4 "Retire email reports, .idea cleanup" into two lines; `.idea cleanup` marked CLOSED 2026-06-03.

---

## Key Findings (May Review)

Worth carrying into June review and the July evaluation:

1. **Hard stop overshoot is structural, not configurable.** 12/12 May hard stops fired worse than the -8% trigger threshold (avg -8.27%, worst -8.93%). Verified the bot config is correct: `hard_stop_max_pct: 0.08` is enforced; the overshoot comes from the trigger-and-MARKET-fill pattern, not config drift. The 5-second `check_interval_sec` throttle plus WebSocket tick granularity on illiquid micro-caps explains the gap.
2. **Deep-indicator pattern strengthens on independent sample.** Mid-collection (through 2026-05-23) found 40% vs 10.5% HS rate; May-only (independent sample, n=41) found 37.9% vs 8.3%. Both directions agree at ~4× discrimination. Sample size in the deep-confirmation bucket nearly tripled (n=12 in May vs n=19 cumulative-through-May-23).
3. **PerformanceFilter is not excluding broken symbols.** PLAY/INJ/AI account for 8/12 May hard stops ($-49.55 / $-75.56 = 67%). Both PLAY and INJ hit 2/3 of the filter's exclusion criteria (WR<30%, avg<-$5) but neither hits the third (total<-$50). Strong suspicion: criteria are AND'd; if they were OR'd, both would be excluded. **New open question for July: verify in `v2/plugins/risk/performance_filter.py`.** Logged in MEMORY.md and `monthly_review_2026-05.md`.
4. **Buy Swing flipped direction in May.** Cumulative-through-May-23 was neutral (83.1 win / 81.3 HS); May-only shows 100 win / 83.3 HS. Could be regime noise. Parked.
5. **Effective May was 23 trading days, not 31.** The 2026-05-23 → 2026-05-29 silent-strategy event (regime + volume guardrails correctly idle) ate the last week.

---

## Security Audit — Full Detail

**How the audit started:** Conversation about where to commit the May review memory file → recommended public/private split → user asked "is there anything unsafe about keeping the project public?" → triggered systematic audit.

**Findings (initial scan):**
- `Config/websocket_api_info.json` — Real Coinbase Advanced Trade API EC private key (key ID `d827d99f-8f4c-4862-938f-3da26d875dce`, org `75baf820-7e82-444a-ba30-b4ffd3859a95`). Coinbase trading was dormant but the key was presumably still active until rotated.
- `.claude/testing/test_peak_tracking_edge.py:187` — Hardcoded `7317botTrade4ssm` (production DB password).
- `scripts/utils/investigate_sl_issue.py:22` — Looked like a leak but verified as the literal placeholder `"your_secure_password_here"`. Safe.
- `v2/tests/test_kraken_exchange.py:24` — Test fixture `dGVzdF9zZWNyZXQ=` (base64 of `test_secret`). Safe.

**Findings (extended scan after the first git rm):**
Searching for the literal DB password value revealed it appeared in **10 tracked files** at HEAD, not just one. Tracked everywhere from January session notes to currently-live backtest scripts.

**Mechanism — why a leak that big slipped through:**
- `.gitignore` had a line for `kraken_api_info.json` but no rule for `websocket_api_info.json`. File was committed before the gitignore could catch it.
- DB password leaks happened across many sessions where someone wrote a one-off SQL script and hardcoded the connection string. No code review caught the pattern.

**Remediation pattern adopted:**
- Live scripts → `os.environ['DB_PASSWORD']` (requires user to set the env var or use `python-dotenv` to load `.env`).
- Archive / inert code → literal `***REDACTED***` (still won't auth, makes the intent obvious to readers).
- `.gitignore` hardened with prophylactic globs: `Config/*_api_info.json`, `Config/*.key`, `Config/*.pem`.
- Old values remain in git history forever. Decision: rely on rotation, do NOT rewrite history (BFG / `git filter-repo` is destructive on a public repo with potential forks).

---

## Problems Encountered & Solutions

| Problem | Solution |
|---|---|
| Heredoc-over-ssh `<<'SQL'` for the May review queries returned empty output | Switched to `scp file && ssh "docker cp ... && psql -f"` pattern |
| Naive SQL `ROW_NUMBER` FIFO pairing mispaired April hard stops (got -5.13% avg vs trigger-stamped -8.99%; one outlier at -24.75% from cross-window position) | Wrote a minimal Python FIFO matcher mirroring `v2/dashboard/trades.py` logic, dumped all 553 fills as JSONL, ran locally. Numbers reconciled to within 0.2% of trigger-stamped values. |
| ROUND() type-cast error in PostgreSQL aggregate query (`round(double precision, integer) does not exist`) | Cast all SUM/AVG results to `::numeric` before ROUND |
| `.current-session` symlink showed up as TYPE-changed in git status | Restored via `git checkout HEAD -- .current-session`; the session system overwrites it on session-start anyway |
| Stale `.env.backup-*` files on prod still had the old password | Deleted them after rotation as separate cleanup |
| User asked where to "commit" the May review memory — memory dir not in any git repo | Recommended against publishing memory files on public repo (competitive intelligence); they stay in `~/.claude/projects/.../memory/` only |

---

## Dependencies / Configuration Changes

- **Dependencies:** none added or removed.
- **Production `.env`:** `DB_PASSWORD` changed; no other keys modified.
- **`.gitignore`:** added 4 lines (`Config/*_api_info.json`, `Config/*.key`, `Config/*.pem`, `.claude/testing/test_peak_tracking_edge.py`) and 1 explicit file (`Config/websocket_api_info.json`).
- **No `docker-compose.aws.yml` changes** — the compose file already references `${DB_PASSWORD}` correctly; rotating only the value preserved the contract.

---

## What Wasn't Done / Future Items

1. **Old Postgres password still in git history.** No history rewrite was performed. Acceptable because it's a dead credential. If you ever want a fully clean history (e.g., before sharing the repo more broadly), that's a separate destructive operation requiring `git filter-repo` + force-push + coordinating with anyone who has clones/forks.
2. **PerformanceFilter behavior verification deferred to July.** Code at `v2/plugins/risk/performance_filter.py` — need to check whether exclusion criteria are AND'd (current symptom) vs OR'd (would have caught PLAY/INJ). Logged in `monthly_review_2026-05.md` and as a July open question.
3. **End-of-June review** — the next monthly review using the same template. Same SQL pattern, same memory file naming convention.
4. **Email reports retirement** — still P4 in MEMORY.md, untouched this session.
5. **Local laptop dev scripts now require `DB_PASSWORD` in env.** `scripts/check_backtest_data.py` and `backtest/download_historical_data.py` no longer hardcode the password — user needs to set `DB_PASSWORD` in their local shell or `.env` to use them.

---

## Lessons Learned

1. **Public-repo discipline is a separate axis from credential discipline.** I instinctively suggested putting the May review memory in `.claude/memory/` for versioning. That would have leaked competitive intelligence (P&L numbers, hypothesis details, broken-symbol callouts) on a public repo. Now codified in `feedback_public_repo_discipline.md`.
2. **Don't trust naive SQL FIFO for round-trip P&L analysis.** The production matcher in `v2/dashboard/trades.py` handles partial fills and cross-window positions correctly; a `ROW_NUMBER` join is approximate at best. For any rigorous analysis, run the production matcher against a JSONL dump.
3. **Trigger-stamped metadata is the authoritative source for hard-stop %.** `metadata->>'pnl_pct'` is computed by the exit manager at trigger time using the actual position avg_entry_price. Post-hoc FIFO reconstruction can drift. When the two disagree, trust the trigger stamp.
4. **`POSTGRES_PASSWORD` env var doesn't update existing Postgres auth.** Postgres only uses it on initial DB creation; the data volume persists existing auth. Rotation requires `ALTER USER`, not just env var update + container restart.
5. **`docker compose up -d --force-recreate v2-kraken dashboard` may also recreate `db`** if compose detects an env interpolation change anywhere in the file. Fine because the data volume persists, but worth knowing — it isn't a strictly-targeted recreate.

---

## Tips for Future Devs / AI Assistants

- Read `MEMORY.md` first. The Critical Caveats section now leads with the public-repo discipline. Don't skip it before staging anything.
- Before any commit that touches a script connecting to a database, confirm credentials come from `os.environ` (or `load_dotenv()`). Pattern reference: `scripts/check_backtest_data.py:21`.
- Monthly reviews go in `~/.claude/projects/.../memory/monthly_review_YYYY-MM.md`. Template is documented in MEMORY.md "Monthly Reviews" section. **Do NOT copy these to the repo.**
- For analytical SQL on `v2_fills`: always run FIFO pairing across **full history**, then filter pairs by `sell_ts`. Filtering buys/sells by date BEFORE pairing breaks the matching. Production logic: `v2/dashboard/trades.py:_match_round_trips`.
- Hard-stop trigger metadata schema (in `v2_fills.metadata` for sell-side hard_stop fills): `pnl_pct` (fee-aware %), `pnl_raw_pct`, `threshold_pct` (negative; -8.0 = ATR ceiling clamp), `atr_pct`, `hard_stop_mode`, `avg_entry`.
- If you need to rotate a credential: ALTER first, then update `.env`, then `--force-recreate` consumers. db stays up; data volume persists.

---

## Status at End of Session

- Branch `main` clean, pushed.
- Production healthy: db + v2-kraken + dashboard + caddy all up. v2-kraken connected to DB and trading (no positions opened this session but pair discovery active and probes are running).
- May review memory file created and indexed.
- Public-repo discipline rule established and enforced in MEMORY.md Critical Caveats.
- Next planned action: end-of-June monthly review (same template, append findings to memory).
