# Session: Sonnet 4.6 model upgrade + Bot Health page

**Date:** 2026-05-16
**Duration:** ~2h (first commit 11:07 PT → last commit 12:53 PT)
**Branch:** main
**Status:** Complete — all changes shipped, deployed, and documented

---

## Why this session happened

User received an Anthropic email announcing **Claude Sonnet 4 retirement on 2026-06-15** and asked whether the codebase needed any changes. Triage uncovered one production usage. After fixing that, the user asked what was open on the backlog. Recommendation: pick up the **Bot Health dashboard page** (Priority 3 in `open_work_items.md`, the lowest-effort meaningful item that doesn't risk contaminating the April→July live-data collection window).

---

## What shipped

### 1. Sonnet 4 → Sonnet 4.6 model upgrade

| Commit | Change |
|--------|--------|
| `13d64bf` | `v2/dashboard/ai_summary.py:229` — `claude-sonnet-4-20250514` → `claude-sonnet-4-6` |
| `e339ae5` | `v2/dashboard/pages/executive_summary.py:83` — footer caption synced to "Sonnet 4.6" |

The only Claude model reference in active code was the Executive Summary page. Verified via `grep` across the whole tree (excluding `archive/`, `.claude/sessions/`, `.git/`) — nothing else hardcoded a model ID. Rationale for picking Sonnet 4.6 over Haiku 4.5 / Opus 4.7: same tier as the retired model, drop-in compatible with the existing `messages.create(...)` call, better quality than Haiku for the 1k-token interpretive summary, no need to pay Opus prices for a 5-minute-cached job.

User confirmed the page regenerated successfully under the new model.

### 2. Bot Health dashboard page (Page 5)

| Commit | Change |
|--------|--------|
| `de6ab9f` | New `v2/dashboard/pages/health.py` (427 lines), replaced 2-line stub |
| `0fc778d` | Bugfixes after first deploy (see "Bugs found and fixed" below) |

**Six panels, all sourced from data already in Postgres:**

1. **Status** — bot freshness pill (Active ≤60m / Idle ≤6h / Stale otherwise, based on max(last_order_ts, last_fill_ts)), open positions count, orders today (UTC), lifetime orders.
2. **Recent Activity** — last order / fill / cancel timestamps with relative ages, plus 24h / 7d order counts and avg orders/day.
3. **Daily Order Submissions (30d)** — stacked bar of buys / sells / cancels per day. Approximates the bot's daily signal-generation rate.
4. **Active Symbols (7d)** — per-symbol fill counts (buys, sells) and most-recent fill timestamp. Sourced from `v2_fills` (source of truth — see bug #2 below).
5. **Equity Curve** — cumulative realized net P&L from FIFO-matched round trips (reuses `v2.dashboard.trades.collect_round_trips`). Sub-metrics: all-time realized P&L, round-trip count, drawdown-from-peak.
6. **Recent Orders** — last 20 orders with **derived** status (`filled` / `open` / `cancelled` / `partial` / `partial+cancelled`) via `LEFT JOIN v2_fills`.

**Cache pattern:** `@st.cache_data(ttl=30)` for status panel, `ttl=60` for everything else. All cached fetchers return plain `dict`s (see bug #1).

### 3. Documentation

| Commit | Change |
|--------|--------|
| `bf0ac03` | `docs/SYSTEM_CONTEXT.md` — Page 5 section in §13, `v2_orders.status` caveat in §10, "Currently Live" bumped to 2026-05-16 + 5 pages, two changelog rows |

**Memory updates (not committed — these live in user-private `~/.claude/projects/...`):**
- `MEMORY.md` — closed Bot Health line item, added new P3 entry for the persistence fix
- `open_work_items.md` — closed Bot Health with deployment notes, added new "Priority 3 — v2_orders.status Persistence Fix" section

---

## Bugs found and fixed during this session

### Bug 1: `UnserializableReturnValueError` on first Bot Health deploy

**Symptom:** Page crashed on load with `Cannot serialize the return value (of type list) in _get_daily_orders()`.

**Cause:** `@st.cache_data` uses pickle, but `asyncpg.Record` (returned by `pool.fetch()`) is not picklable.

**Fix:** All cached fetchers now do `[dict(r) for r in rows]` before returning. (Fixed in `0fc778d`.)

**Lesson for future devs:** Any new cached function that calls `pool.fetch()` must convert to plain dicts. `pool.fetchrow()` followed by `dict(row)` is also safe. Streamlit's `@st.cache_data` is strict about pickling; `@st.cache_resource` skips pickling but is meant for connection pools, not query results.

### Bug 2: `v2_orders.status` is unreliable — 94 "open" orders most of which were actually filled

**Symptom:** User noticed the first Bot Health deploy reported 119 lifetime orders with 94 still "open" — not plausible given the bot has been running for weeks.

**Cause:** `v2/plugins/exchanges/kraken.py:587` emits a `FillEvent` when an order fills via the WebSocket private channel but **never re-emits an `OrderEvent` with status=FILLED**. The persistence layer's `record_order()` UPSERTS on `order_id`, but since no second OrderEvent ever fires, `v2_orders.status` stays at `open` forever for any order that filled. `v2_orders.status` only ever takes the values `open` (at submission) or `cancelled` (on explicit cancel). The real distribution at 2026-05-16: 94 `open` (mostly filled), 25 `cancelled`. `v2_fills` had 517 rows over the same orders, so it's clearly the source of truth.

**Fix (read-only, dashboard-side):**
- `_get_active_symbols` now queries `v2_fills` directly instead of `v2_orders`.
- `_get_recent_orders` does `LEFT JOIN v2_fills ON order_id`, sums `f.qty`, and a new `_derive_status(raw_status, filled_qty, qty)` helper resolves the true state (`filled` / `open` / `partial` / `cancelled` / `partial+cancelled`).
- "Daily Order Activity" renamed to "Daily Order Submissions" with a caption clarifying it measures signal rate, not order outcomes.
- A caption on the Recent Orders table explains the v2_orders.status quirk.

**Proper fix (deferred):** Emit an `OrderEvent(status=FILLED)` from `kraken.py` after `self._bus.publish(FillEvent(fill=fill))`. One-line behavior addition; the existing UPSERT in `record_order()` picks it up via `ON CONFLICT (order_id) DO UPDATE SET status = EXCLUDED.status`. **Held back until post-July** to preserve the v2-kraken container during the live-data collection window. Tracked as Priority 3 in `open_work_items.md`.

---

## Deployment steps (followed `CLAUDE.md` standard workflow each time)

For each commit that touched dashboard code:
```bash
git push origin main
ssh bottrader-aws "cd /opt/bot && git pull origin main"
ssh bottrader-aws "cd /opt/bot && docker compose -f docker-compose.aws.yml up -d --build dashboard"
```

For the docs-only commit (`bf0ac03`): push only, no rebuild.

**Key invariant:** dashboard container has code baked into the image, so `--build` is required (not just `restart`). Same rule applies to `v2-kraken`.

After each deploy, verified via `docker logs --tail 20 dashboard` and `git log --oneline -3` on AWS.

---

## Scope decisions / things deliberately not built

These came up during planning and are documented for future continuity:

### Container up/down status panel
**Why deferred:** The dashboard container has no Docker socket access (and shouldn't — it's public-facing via Caddy at https://bottrader.trade). Surfacing real container health needs either (a) a sidecar that writes container state to DB, or (b) a server-side health endpoint Caddy could expose. For now, the freshness pill (DB activity proxy) is adequate; SSH gives true container state.

### Risk events panel (vetoes, circuit breaker trips)
**Why deferred:** `v2/plugins/observability/daily_report_v2/collectors/risk_events.py` (`RiskEventAccumulator`) is in-process memory only — counts get reset on restart and are never written to the DB. Adding a `v2_risk_events` table is a clean follow-up; the event types and accumulator API are already well-defined. Estimated effort: small (one new table, one observer that subscribes to the same events and persists them).

### v2_orders.status persistence fix
**Why deferred:** One-line change to `kraken.py`, no strategy impact. But the standing policy from the fee/sizing analysis (closed 2026-04-12) is **no changes to v2-kraken during the April→July 2026 collection window** to keep the experimental regime clean. The dashboard's LEFT JOIN workaround is acceptable until post-July.

### Acting on the AI Executive Summary's "suspend trading + drop RAVE-USD" recommendation
**Why deferred:** Conflicts with the same no-parameter-changes policy. RAVE-USD concentration is an *expected* feature of the current collection window (already noted in `memory/rave_usd_concentration.md`), not a surprise. Flagged to user explicitly: "Treat the summary as commentary, not a directive."

---

## Files changed

```
docs/SYSTEM_CONTEXT.md                  |  28 +-
v2/dashboard/ai_summary.py              |   2 +-
v2/dashboard/pages/executive_summary.py |   2 +-
v2/dashboard/pages/health.py            | 471 +++++++++++++++++++++++++++++++-
4 files changed, 493 insertions(+), 10 deletions(-)
```

Memory files (user-private, not in repo):
- `~/.claude/projects/-Users-Manny-Python-Projects-BotTrader/memory/MEMORY.md`
- `~/.claude/projects/-Users-Manny-Python-Projects-BotTrader/memory/open_work_items.md`

---

## Final git status

```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  typechange: .claude/sessions/.current-session
  modified:   .idea/BotTrader.iml

Untracked files:
  .claude/sessions/2026-05-12-project-audit-pass3-pass4.md
```

The two unstaged items and the untracked session file are **pre-existing from before this session** — they were already in the working tree at session start and are unrelated to today's work.

---

## Open backlog after this session (P-ordered)

1. **P1** — July 2026 live-data review (target: July). No changes until then.
2. **P2** — Adaptive TTL Phase 2 (scale buy order TTL by ATR).
3. **P3** — `v2_orders.status` persistence fix (this session's new addition).
4. **P3** — Config Editor dashboard page.
5. **P4** — Retire email reports + `.idea/` cleanup.

Bot Health page is closed.

---

## Lessons / tips for the next dev or AI

1. **Streamlit caching gotcha:** `@st.cache_data` requires pickle-serializable returns. `asyncpg.Record` is not. Convert to `dict(r)` before returning from any cached fetcher. See `_get_daily_orders` in `health.py` for the pattern.

2. **Don't trust `v2_orders.status`** until the kraken.py fix lands. Always derive true status by joining `v2_fills` on `order_id`. The `_derive_status` helper in `health.py` is reusable.

3. **`v2_fills` is the source of truth for executed trades.** `v2_orders` is essentially an audit log of submissions and cancellations. The FIFO matcher in `trades.py` already uses `v2_fills` correctly — follow that pattern.

4. **Dashboard deploys need `--build`.** Same as v2-kraken. Both bake code into their images.

5. **Resist the AI Executive Summary's recommendations during the collection window.** The summary will often surface known-and-expected issues (RAVE concentration, hard-stop rate above backtest baseline) and recommend reactive parameter changes. The policy is to wait for the July evaluation. The summary is interesting commentary, not a directive.

6. **The dashboard page nav lives in `v2/dashboard/app.py`** — six pages already wired up; no app.py change was needed for the Bot Health implementation since the stub was already in the nav.

7. **Schema sanity-check workflow that worked well this session:** before writing a new query, run it via `ssh bottrader-aws "docker exec db psql -U bot_user -d bot_trader_db -c '<query>'"` to confirm shapes and edge cases. Cheaper than a build/deploy cycle.

---

## Commits at a glance

```
bf0ac03 docs: update SYSTEM_CONTEXT for Bot Health + Sonnet 4.6
0fc778d fix(dashboard): Bot Health cache + accurate order status
de6ab9f feat(dashboard): implement Bot Health page
e339ae5 fix(dashboard): update Executive Summary footer to Sonnet 4.6
13d64bf fix(dashboard): upgrade Executive Summary model to Sonnet 4.6
```
