# v2 Audit Follow-Ups — Handoff

**Date:** 2026-09-18
**Status:** Not started. Every item below was found during the 2026-09-18 audit of the v2 UI and was deliberately left out of that session's scope. Nothing here has been attempted.
**Branch:** `main`
**Read first:** `docs/superpowers/plans/2026-09-18-v2-audit-fixes.md` — the plan for the work that *was* done, with the original audit evidence.

## What the 2026-09-18 session already fixed (committed to `main`, NOT pushed, NOT deployed to prod)

> **Provenance — read this before you `git blame`.** These fixes landed in commit **`999e0c4`**, titled *"feat: morning brief pre-scan, wheel scorer, and watchlist technicals fix"*. A different, concurrent session made that commit and swept this session's files into it along with its own morning-brief work. Its message calls the scanner, options and stocks changes "previously-uncommitted morning brief pipeline work". **That description is wrong for the files below.** They are the v2 audit fixes, specified in `docs/superpowers/plans/2026-09-18-v2-audit-fixes.md`. The one exception is the `max_delta` parameter in `src/screener/options.py`, which belongs to the other session. `git blame` on these files points at `999e0c4`; use this table, not that commit message, to find out why a line changed.

| Audit # | Fix | Files |
|---|---|---|
| 1 | Stock screener HTTP 500 from a NaN `Close` row — Watchlist showed "No stocks found" | `src/screener/stocks.py` |
| 2 | CSP scanner recommended contracts with no bid; premium came from stale last trades | `src/screener/options.py` |
| 4 | ETF share holdings invisible in Open Holdings (view only — see item 1 below) | `src/web/v2/wheel.js` |
| 5 | Holding card could show a gain beside a cost basis implying a loss | `src/web/v2/wheel.js` |
| 6 | Two premium figures read as the same quantity | `src/web/v2/wheel.js` |

Finding #3 (wheel freshness) was **verified working on prod** — not fixed. Its latent defects are item 3 below.

---

## Priority order

| # | Item | Why this rank |
|---|---|---|
| 0 | Deploy the committed fixes to prod | Prod still runs the broken screener and scanner |
| 1 | Backend ETF bug in `store.py` | **Wrong money figure on prod right now** (~$415 on DRAM) |
| 2 | `target_premium` always `None` | Silent data loss in every nightly file since the feature shipped |
| 3 | Wheel "Live" badge + frozen DTE | Already showed stale data as live on prod on 2026-09-17 |
| 4 | End-to-end tests for both screener fixes | Two reviewers independently flagged the gap |
| 5 | LEAPS screener uses stale last-trade prices | Same bug class as audit #2 — **unverified** |
| 6 | Small cleanups | Low risk, low value |

---

## 0. Deploy the committed fixes to prod

**Not a bug — a missing step.** The fixes are committed to `main` on dev but are **not pushed and not deployed**. Prod (`market.austin10berge.com`, host `firefly` / 10.0.1.21) still runs the old code.

**Expected on prod until deployed (inferred, not verified):** prod runs the same `stocks.py` and `options.py`, so it should show the same Watchlist 500 and the same untradeable scanner candidates. Confirm read-only first:
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://market.austin10berge.com/api/screener/stocks
```
Caution: that endpoint recomputes the whole screener on every call while the bug is live, because the NaN also stops the result from caching. Call it once, not in a loop.

**⚠️ Pushing `main` ships more than these fixes.** On 2026-09-18 local `main` was 2 commits ahead of `origin/main` (`999e0c4`, `a6a4786`). Both commits also carry a concurrent session's morning-brief work, and `999e0c4`'s own message lists **known test failures it introduced**: `test_synthesis_macro` (collection error), `test_llm_synthesis::TestCallGemini` (3), and `test_csp_scanner_integration` (1). A clean checkout of the pre-existing `HEAD` plus only the audit fixes passed with **0 failures**. So those failures come from the other work, not from the fixes. Decide whether that work is ready for prod **before** you push. The audit fixes cannot be pushed on their own without rewriting those two commits.

**Deploy (from CLAUDE.md — follow it exactly):**
1. Push from dev: `git push origin main`
2. On prod, check for local hotfixes first: `ssh firefly 'sudo -n git -C /root/market-intelligence status --short'`. Stash any overlap (`git stash push -u -- <files>`) rather than assuming a clean pull.
3. `ssh firefly 'sudo -n git -C /root/market-intelligence pull origin main'`
4. **Prod bakes `src/` into the image — a restart alone does NOT pick up code.** Rebuild, then start:
   ```bash
   sudo -n docker compose -f /root/market-intelligence/docker-compose.yml build api dashboard
   sudo -n docker compose -f /root/market-intelligence/docker-compose.yml up -d --no-build api dashboard
   ```
5. Verify: Watchlist renders rows; Scanner shows no card with `IV 0.0%` or `Δ—`; Wheel shows DRAM under Open Holdings.

---

## 1. Backend ETF bug — `src/wheel_tracker/store.py` — **wrong money figure on prod**

**Symptom.** Symbol Performance understates any ETF ticker's return by its share P&L. Ticker counts exclude ETF-only positions.

**Evidence (verified on both hosts, 2026-09-18):**
```
dev:   DRAM  total_return 776.72  ==  total_premium 776.72
prod:  DRAM  total_return 863.73  ==  total_premium 863.73
```
`total_return == total_premium` exactly means the share leg contributes zero. On prod, DRAM's 100 shares carried **+$415.34** unrealized at the time, missing from Symbol Performance.

**Root cause.** Schwab classifies ETFs as `COLLECTIVE_INVESTMENT`, not `EQUITY`. The 2026-09-18 session fixed this in the **view** (`src/web/v2/wheel.js`, constant `HOLDING_ASSET_TYPES`). The same exact-match test survives at six sites in the store layer:

| Line | Function | Effect |
|---|---|---|
| 263 | `_strategy_label` | ETF share trades get a raw instruction label, not a friendly one |
| 471 | `_reconcile_equity` | Skips ETF share trades — no share P&L is ever reconciled |
| 557 | `get_ticker_ledger` | SQL `IN ('EQUITY','OPTION')` drops ETF share trades from the ledger entirely |
| 579 | `get_ticker_ledger` | ETF positions not treated as share positions — no unrealized P&L |
| 705 | `get_wheel_stats` | `total_tickers` omits ETF-only tickers |
| 711 | `get_wheel_stats` | `active_tickers` omits ETF positions |

**Knock-on effect to check.** `get_monthly_realized_pnl` builds on `get_ticker_ledger`, so **realized share P&L from selling or being called away on an ETF is probably missing from Monthly Realized too.** Not verified — check it.

**Suggested fix.** Add one module constant and use it at all six sites, mirroring the JS constant so the two layers cannot drift:
```python
# Schwab classifies ETFs as COLLECTIVE_INVESTMENT, not EQUITY. Both are share
# positions the wheel writes calls against. Keep in sync with
# HOLDING_ASSET_TYPES in src/web/v2/wheel.js. MUTUAL_FUND is excluded on
# purpose — that is the SWVXX cash sweep.
SHARE_ASSET_TYPES = ("EQUITY", "COLLECTIVE_INVESTMENT")
```
For the two SQL sites, build the placeholder list from the constant rather than hardcoding a second copy.

**Verify.** `tests/test_wheel_tracker_store.py` exists — add a case with a `COLLECTIVE_INVESTMENT` share trade and position, and assert `total_return` includes the share leg. Then on dev: DRAM's `total_return` must exceed its `total_premium`.
**Dev data caveat:** dev's wheel data is frozen at 2026-08-27 (see Gotchas), so its absolute numbers will not match prod. Assert the *relationship*, not a value.

---

## 2. `target_premium` is always `None` — `src/screener/csp_scan_nightly.py:105`

**Symptom.** Every nightly `data/wheel-candidates/YYYY-MM-DD.json` has `"target_premium": null` on every candidate.

**Evidence.** `data/wheel-candidates/2026-09-17.json` — all 3 candidates (HL, HL, NU) have `target_premium: None`.

**Root cause.** Line 105 reads `"target_premium": c.get("lastPrice")`. Neither the CSP scanner nor `wheel_scorer` ever emits a `lastPrice` key. The only `lastPrice` reads in the codebase are yfinance option-chain fields inside `src/screener/options.py`. This is committed code, introduced by `e7b3cd2` ("feat: add nightly CSP scan script"), so it has probably been `None` since that feature shipped.

**Fix.** One line: `"target_premium": c.get("premium")`. This is now safe: since 2026-09-18, `premium` is `round((bid + ask) / 2, 2)` from a two-sided quote. Before that fix it could have been a months-stale trade price.

**⚠️ This file carried someone else's uncommitted change on 2026-09-18** (`top_n=10` on the `score_wheel_candidates` call, ~line 166). Run `git diff src/screener/csp_scan_nightly.py` before editing, and stage only your own hunk.

**Verify.** Run `docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm pipeline` (or the nightly scan entry point) and confirm the newest JSON has numeric `target_premium` values.

---

## 3. Wheel "Live" badge and frozen DTE — audit finding #3

**Symptom.** The Wheel page can show stale positions and wrong days-to-expiry while its badge reads **Live**.

**Evidence that it already happened on prod.** On 2026-09-17 the nightly pipeline logged:
```
Could not parse accounts response: ... unsupported_token_type: 400 Bad Request ... Refresh token ...
wheel_tracker sync complete: {'accounts_synced': 0, 'trades_imported': 0, 'positions_refreshed': 0}
```
So on 2026-09-17 prod served 2026-09-16 positions under a "Live" badge. (Query Loki: `{service_name=~"market-intelligence-pipeline-run-.*"} |~ "(?i)wheel"`.)

**Two root causes:**
1. **The badge is hardcoded.** `src/web/v2/wheel.js` sets `badge.className = 'data-freshness-badge fresh'; badge.textContent = 'Live'` after any successful fetch. It ignores `refreshed_at`, which `/api/wheel/positions` already returns on every row.
2. **DTE is stored, not computed.** `src/wheel_tracker/sync.py` writes `dte` at sync time. `wheel.js`'s `renderOpenTrades` renders `p.dte` directly. A missed sync leaves every DTE frozen.

**Fix.**
- Drive the badge from the oldest `refreshed_at`. **Copy the Scanner's badge** — it already does this correctly ("cached · 3m ago", `src/web/v2/scanner.js`). Show a stale state when data is older than one trading day.
- Compute DTE in the browser from `expiration`, or in the API response, rather than trusting the stored column.

**Verify.** Dev's wheel data is frozen at 2026-08-27, so dev is the ideal fixture: the badge must read stale, and SOFI `2026-08-28` / DRAM `2026-08-31` must show as expired rather than "DTE 1" / "DTE 4".

---

## 4. End-to-end tests for both screener fixes

**Gap.** The 2026-09-18 plan specified only helper-level unit tests (`tests/test_stock_screener_nan.py`, `tests/test_options_quality_gates.py`). Nothing drives the full function, so a future edit that deletes or reorders a call site would pass every test while reintroducing the bug. **Two separate reviewers flagged this independently.**

**Add:**
- `screen_stocks()` with a mocked `yf.Ticker` whose `history()` returns a frame ending in a NaN-`Close` row. Assert the returned rows contain no NaN/inf floats and that `json.dumps(rows, allow_nan=False)` succeeds. That exact call is what failed in production.
- `screen_csp_candidates()` with a mocked yfinance chain and a mocked Alpaca snapshot response (`httpx.Client.get`) containing one two-sided contract and one `bid=0` contract. Assert only the two-sided one survives and that its `premium == round((bid + ask) / 2, 2)`. `tests/test_options.py` already shows how to mock `options.yf.Ticker`.

---

## 5. LEAPS screener uses stale last-trade prices — **UNVERIFIED**

**Pattern match only — not yet shown to cause a problem.** `screen_leaps_candidates` in `src/screener/options.py` (~line 714) sets `premium = call_data["lastPrice"]` from yfinance. That is the same stale-last-trade pattern that made audit #2 serious: on 2026-09-17 a CSP contract's last trade was five months old. LEAPS are longer-dated and often less liquid, so the risk may be higher, not lower.

It also emits `"premium": float(premium)` unrounded — cosmetic only, because every display site formats to 2 dp.

**Investigate before fixing.** Pull live LEAPS candidates and compare each `lastPrice` against the current bid/ask and the last-trade timestamp. The CSP fix's helpers (`_usable_quote`, `_mid_price`, `_spread_pct`) are reusable if the problem is real — but LEAPS read from yfinance, not Alpaca. **yfinance returns `bid = ask = 0` outside market hours**, so test during the session.

---

## 6. Small cleanups

- **GLAB is delisted** and still in the stock watchlist. It returns a Yahoo 404 on every scan and is why the Watchlist shows 11 rows for 12 tickers. Remove it via the UI's "Edit watchlists" or `POST /api/watchlist/stock`.
- **Dead code.** `src/screener/stocks.py` (~line 887): `"price": round(...) if not pd.isna(current_price) else 0.0`. Since `_drop_incomplete_rows`, `current_price` can no longer be NaN. Harmless — remove only when touching the area.
- **Unlogged data repair.** `_drop_incomplete_rows` drops *interior* NaN-`Close` rows silently, not just the trailing in-progress one. Dropping is correct (pandas_ta windows are positional, so an interior NaN poisons ~199 later SMA/EMA values), but a `logger.debug` line with the count would make real data gaps visible.
- **Scanner rejection counters.** Now in the INFO summary line (`no_bid=…, no_delta=…`). No action — noted so no one re-adds them.
- **Resize-listener leak.** `mountPerfChart` in `src/web/v2/wheel.js` adds a `window` resize listener on every mount, and `teardown()` is empty, so it is never removed and `chart.remove()` is never called. Measured as not user-visible (1 chart in the DOM after 3 visits). Fix by storing the chart and listener and cleaning both up in `teardown()`.

---

## Gotchas learned on 2026-09-18 — read before starting

- **Python runs only in Docker.** No host virtualenv.
- **Every `docker compose` command on dev needs both compose files**, or the container does not see your edited `src/` and `tests/`:
  `docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm test python3 -m pytest <path> -v`
- **After a Python edit, use `restart api`, not `up -d --no-build api`.** `up -d` is a no-op when the compose config has not changed, so it does not reload bind-mounted code. (CLAUDE.md's `up -d --no-build` is correct only in its own context — switching the mounted worktree *does* change the config and recreate the container.)
- **The test image build is slow.** Build and run as separate commands, each with an explicit long timeout. Never background them.
- **The Scanner UI and the bare API endpoint use different Redis cache keys.** The UI sends a query string (`max_price=150`, …); the bare `/api/screener/csp-scan` uses defaults (`max_price=500`). Clearing one does not refresh the other. Use `browser_network_requests` to find the exact URL the UI calls.
- **Measure scanner candidate counts during market hours only.** The same code returned 13 candidates after the close and 40 during the session. Two-sided quotes thin out after hours, and yfinance returns `bid = ask = 0` then.
- **Dev's wheel data is frozen at 2026-08-27.** The nightly pipeline runs only on prod (host `finance` in Loki), never on `ai-dev`. For true wheel numbers, read prod's API read-only. For fixtures, dev's stale data is useful: it contains an ETF holding (DRAM) and internally inconsistent P&L rows.
- **More than one session may be working in this tree at once.** On 2026-09-18 a concurrent session edited `src/screener/csp_morning_scan.py` at 15:10 UTC, then at 15:29 UTC committed everything in the working tree, including another session's in-progress files. Run `git status`, `git diff` and `git log -3` before you touch a file or commit, and stage only your own hunks. If `options.py` or another file mixes your hunks with someone else's, build the staged version by applying your own patch to `git show HEAD:<file>`, then check that the leftover diff is only the other hunks.
- **Test baseline on `main` at `a6a4786`:** 733 passed, 4 failed, 1 collection error. The 4 failures are in `test_csp_scanner_integration.py` (1) and `test_llm_synthesis.py::TestCallGemini` (3); the collection error is `test_synthesis_macro.py`. **None are caused by the 2026-09-18 audit fixes.** A clean tree of `cdc8040` plus only the audit fixes passed with 0 failures. `999e0c4`'s own commit message attributes all five to its morning-brief work ("tests not yet updated for the behavior changes").
- **A clean checkout of `cdc8040` alone could not import `src.screener.wheel_scorer`**: `csp_scan_nightly.py` imported a file that was not committed yet. `999e0c4` fixed this by committing `wheel_scorer.py`. If you bisect across that range, expect `tests/test_csp_scan_nightly.py` to fail collection on the older side.
- **UI verification uses the Playwright MCP at 390×844** (the user's phone layout). Resize before any screenshot. Always test on `dev-mi.austin10berge.com`.
