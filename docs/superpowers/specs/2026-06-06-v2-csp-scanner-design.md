# V2 CSP Scanner — Design Spec

**Date:** 2026-06-06
**Branch:** `redesign/ui-refresh`
**Status:** Approved (design); pending implementation plan

## Goal

Replace the v2 Scanner handoff card with a **full, native, faithful port** of the v1 CSP Universe Scanner, built into the v2 mobile-first SPA. Every v1 control and result section is preserved, reorganized for the v2 shell (fixed status bar + bottom nav) and styled with the v2 visual language via the **frontend-design** skill.

## Decisions (from brainstorming)

| Topic | Decision |
|-------|----------|
| Scope | **Full faithful port** — all ~18 params, conditions picker, sector picker, watchlist-universe toggle, DTE, 4-stage funnel, Stock Performance, paginated results |
| Form layout | **Filter sheet** — results-first main view; all controls live in a full-screen slide-up overlay |
| Results | **Reuse v2 `option-card` component** for candidates **+ keep** Stock Performance as a collapsible block |
| First visit | **Auto-load cached snapshot** — fetch with saved/default params on first open (instant if cached EOD result exists; triggers live scan if not), matching the lazy-load pattern of other v2 views |
| Param persistence | **Shared localStorage key** (`market-intelligence:csp-scanner-params`) — params carry between v1 and v2 scanners |
| Code organization | **Separate module** `src/web/v2/scanner.js` exposing `ScannerView.render(rootEl)`; router in `app.js` delegates the `scanner` case to it |

## Architecture & Files

- **`src/web/v2/scanner.js`** (new) — all scanner logic: filter state, query-string building, scan fetch + polling fallback, funnel render, candidate-list render (v2 option-cards), Stock Performance render, pagination, filter-sheet open/close/apply, data-freshness + force-rescan. Exposes `ScannerView.render(rootEl)`; tears down timers (`scanPollId`) on view switch.
- **`src/web/v2/app.js`** — router `case 'scanner'` calls `ScannerView.render(mainContent)` instead of `renderHandoffView('Scanner', …)`. Backtester keeps its handoff card. Router teardown calls a `ScannerView` cleanup hook to clear any active poll timer when navigating away.
- **`src/web/v2/index.html`** — add `<script src="./scanner.js"></script>` **before** `app.js`; add CSS for filter sheet, funnel strip, active-params chip row, freshness badge.
- **No API changes**, **no Dockerfile change** (the whole `src/web/v2/` dir is COPYed wholesale).

### Reused endpoints
- `GET /api/screener/csp-scan?<params>` — run scan (23h EOD cache; per-param cache key)
- `DELETE /api/screener/csp-scan?<params>` — bust cache (Force Rescan)
- `GET /api/screener/csp-scan/conditions` — technical conditions for the picker
- `GET /api/screener/csp-scan/sectors` — sectors for the picker
- `GET /api/market-data/status` — data freshness badge + stale warning
- `POST /api/market-data/refresh` — Force Rescan optionally refreshes stale data first
- `GET /api/screener/stocks?tickers=…` — Stock Performance section

## View Layout

Three stacked zones inside `#main-content` (status bar + bottom nav stay fixed):

1. **Header bar** — title "CSP Scanner", **data-freshness badge** (green/amber from `/market-data/status`; amber = stale warning with age in hours), **Filters** button showing active non-default filter count (e.g. "Filters · 3").
2. **Active-params summary** — compact horizontal chip row of currently-applied non-default params (`cap ≥ $10B`, `IV ≥ 30`, `RSI ≤ 65`, sector chips, condition chips). Tapping it opens the filter sheet.
3. **Results zone**
   - **Funnel strip** — compact 5-cell row (Universe → Fundamental → Vol → Technical → Candidates) + cache/live status pill ("updated X ago"). Restyled to v2 tokens; horizontally scrollable on narrow screens.
   - **Stock Performance** — collapsible block (collapsed by default on mobile), fetched via `/screener/stocks` for the candidate tickers.
   - **Candidate list** — v2 `option-card` components, paginated (reuse existing prev/next pager pattern).

### Filter sheet (full-screen slide-up overlay)
Sections mirroring v1:
- **Universe** — min market cap, max price, min/max beta, min IV/RV, max RSI, min/max ADX, min/max DTE (number inputs)
- **Fundamentals** — min FCF, max debt/equity, min revenue growth, min earnings growth, min dividend yield, max forward P/E, max PEG
- **Technical Conditions** — condition picker chips from `/conditions`
- **Sectors** — checkboxes from `/sectors`
- **Watchlist-universe toggle** — restrict to S&P 500 + NASDAQ 100 only
- Sticky footer: **Apply & Scan** (write params → close sheet → run scan) and **Force Rescan** (DELETE cache → refresh market data if stale → rescan)

All sheet sections built with **frontend-design** to match v2 tokens (IBM Plex Mono/Sans Condensed, CSS custom properties, existing card/chip styles).

## Data Flow & State

Module-level state in `scanner.js` (mirrors v1 `_state`):
- `params` — loaded from shared localStorage key on init, merged over defaults; written on Apply.
- `scanPollId` — interval id for the polling fallback; cleared on completion and on view teardown.
- `cspPage` — current results page.
- `lastResult` — last scan payload (funnel summary + candidates + cache meta).

Flow:
1. `render(rootEl)` paints the shell, loads conditions + sectors + freshness in parallel, renders the active-params summary, then **auto-runs a scan** with current params.
2. `runScan()` → `GET /screener/csp-scan?<query>`; if the response signals still-computing, start `scanPollId` to re-fetch until done; on done, render funnel + candidates, then fetch Stock Performance for the returned tickers.
3. **Apply & Scan** writes params → localStorage, re-renders summary, runs scan.
4. **Force Rescan** checks `/market-data/status`; if stale, `POST /market-data/refresh` (with the existing ~20s settle wait); then `DELETE /screener/csp-scan?<query>` and re-run.

## Error Handling & Edge Cases

- **Empty universe / zero candidates** — funnel still renders (shows zeros); candidate list shows an empty-state message; not cached server-side (existing behavior).
- **API/network failure** — each zone degrades independently: freshness badge → "unknown"; scan failure → inline error in results zone with a retry affordance; Stock Performance failure → its block hides quietly (non-critical).
- **Stale market data** — amber freshness badge + warning line with age; Force Rescan offers the refresh path.
- **View teardown mid-scan** — router cleanup clears `scanPollId` so no stray timers run after navigating away.
- **Long scans** — polling fallback (mirrors v1) handles scans exceeding the initial request window.

## Testing

- Frontend is unbuilt vanilla JS; verify via **Playwright MCP** against **dev** (`https://dev-mi.austin10berge.com`) after deploy: open Scanner tab → assert funnel + cards render; open filter sheet → change a param → Apply → assert results update; assert freshness badge state; assert pagination.
- Backend unchanged — no new Python tests required; existing scanner/endpoint tests still cover the API.
- Snapshot-assert via `browser_snapshot` (accessibility tree) for state checks; screenshots for visual confirmation.

## Out of Scope

- No changes to the v1 scanner page (stays as-is).
- Backtester remains a handoff card.
- No new/changed API endpoints or server logic.
