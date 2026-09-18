# Next Steps: Complete v2 Dashboard Upgrade

Two remaining tabs need to be ported from v1 to native v2. Do Task 1 first, verify it, then Task 2.

**Before starting:** Read `src/web/v2/app.js` and `src/web/v2/index.html` to understand the SPA architecture (bottom-nav routing via `switchTab()`, `#main-content` rerendering, module-level data caching). The scanner tab (`src/web/v2/scanner.js`) is the best reference for how a complex native v2 view is structured.

**Font scale rule:** v2 uses ~1.2× v1 sizes (roughly 11–17px body range, 22px display). Match this on any new views.

**Rebuild command:** After any JS/CSS/HTML change: `docker compose build dashboard && docker compose up -d --no-build dashboard`. Test at `https://dev-mi.austin10berge.com/v2/`.

**Browser testing:** Use Playwright MCP (`mcp__playwright__browser_*`) — the JS-rendered frontend requires a real browser. Drive actual interactions, don't just load the page.

---

## Task 1 — Native Backtester tab

`switchTab()` in `app.js:57` currently renders a handoff card → `/backtest.html`. Replace with a native v2 view.

**Source material:** Branch `origin/claude/options-backtester-review-DxKWQ`. Inspect `backtest.html` + `backtest.js` from that branch. It has a tabbed UI (Setup/Strategy/Scale-In), IV re-marking, covered call support, and exit-reason breakdown.

**Implementation:** Port into `src/web/v2/backtester.js` following the `scanner.js` pattern — expose `window.BacktesterView.{render, teardown}`; wire the `backtester` case in `app.js` to delegate to it. Use v2 design language: card components, v2 font scale, dark palette. Status bar and bottom nav must remain fixed.

**Verify:**
1. `docker compose build dashboard && docker compose up -d --no-build dashboard`
2. Playwright: navigate to `https://dev-mi.austin10berge.com/v2/`, click Backtest tab, confirm it renders without JS errors
3. Run an actual backtest to confirm data flows end-to-end
4. Switch away and back — confirm other tabs aren't broken

---

## Task 2 — Technical Analysis tab

No v2 tab exists for this. Add one.

**Source material:** `src/web/technical-analysis.html`, `technical-analysis.js`, `technical-analysis-helpers.js` (v1 page using TradingView Lightweight Charts for per-ticker OHLCV + indicator charts for CSP candidates). Branch `origin/feature/csp-technical-analysis` has related work.

**Implementation:** Add a 5th bottom-nav tab: key `analysis`, label `Charts`, suitable SVG icon. Add entries to the `ICONS` and `LABELS` objects near the top of `app.js`. Implement as `src/web/v2/analysis.js` (exposes `window.AnalysisView.{render, teardown}`); wire into `switchTab()`. Port the TradingView chart logic from v1, adapted to v2 style. Keep the Watchlist/Universe sub-tab structure from v1.

**Verify:**
1. Rebuild dashboard (same command as above)
2. Playwright: click Charts tab, confirm charts render for CSP candidates
3. Switch between Watchlist and Universe sub-tabs
4. Switch away and back — confirm other tabs aren't broken
