# Technical Analysis Page — Universe Tab

**Date:** 2026-05-02
**Status:** Approved

## Overview

Add a "Watchlist | Universe" tab toggle to the Technical Analysis page so users can view candidate cards sourced from either the static watchlist screener or the CSP Universe Scanner, without leaving the page.

## Background

The Technical Analysis page currently shows CSP candidates sourced from `GET /api/screener/csp` (the static watchlist). The CSP Universe Scanner (`GET /api/screener/csp-scan`) produces a broader candidate set using configurable fundamental and technical filters. Both endpoints already return `composite_score` per candidate, but the card UI does not display it yet.

## Changes

### 1. Tab UI (`technical-analysis.html`)

- Add two tab buttons — **Watchlist** and **Universe** — above the indicator config panel.
- Active tab is visually distinguished using the dashboard's existing tab styling.
- Active tab is persisted under the existing `market-intelligence:csp-technical-analysis` localStorage key (merged into the existing settings object).

### 2. Scanner Param Persistence (`scanner.js`)

- `scanner.js` currently holds all state in memory only (`_state.params` + selected conditions). Params are lost on every page reload.
- **On page load:** restore `_state.params` (all numeric filters + conditions array) from `market-intelligence:csp-scanner-params` in localStorage, falling back to hardcoded defaults. Sync the restored values into every form input and condition checkbox so the UI reflects the loaded state.
- **On every input change:** write the full `_state.params` to `market-intelligence:csp-scanner-params` immediately (not just on scan run). This covers numeric filter changes and condition toggle changes.
- This ensures the Scanner page survives page reloads and browser restarts, and also enables the Technical Analysis page to reuse whatever params the user last configured.

### 3. Universe Data Loading (`technical-analysis.js`)

- On first activation of the Universe tab, read scanner params from `market-intelligence:csp-scanner-params` (fall back to `scanner.js` defaults if absent), then fetch `GET /api/screener/csp-scan?<params>`.
- Store the result in a module-level variable (`universeData`). Subsequent tab switches reuse this variable — no re-fetch.
- Show a loading spinner while fetching; show an error message on failure.
- A **Refresh** button on the Universe tab clears `universeData` and re-fetches.

### 4. Composite Score Display (`technical-analysis.js` / `technical-analysis-helpers.js`)

- `composite_score` is already computed and returned by both `/api/screener/csp` and `/api/screener/csp-scan`. No API changes needed.
- Display `composite_score` in every candidate card (both tabs). Add it to the existing metrics grid.
- Cards remain sorted by `composite_score` descending (both endpoints already return results in this order).

### 5. Rendering

- Both tabs use the same `renderCandidateCard()` function — no duplication.
- `dedupeCandidatesBySymbol()` runs on universe results as it does for watchlist results.
- No filter funnel summary is shown on the Universe tab — candidate cards only.

## Data Flow

```
Page load
  └─ fetch watchlist (GET /api/screener/csp)
  └─ render Watchlist tab (default active tab, or last saved)

User clicks Universe tab
  └─ universeData already loaded? → render from memory
  └─ not loaded?
       └─ read params from localStorage (market-intelligence:csp-scanner-params)
       └─ fetch GET /api/screener/csp-scan?<params>   (Redis cache hit if scan ran recently)
       └─ store in universeData
       └─ render candidate cards

User clicks Refresh (Universe tab)
  └─ clear universeData
  └─ re-fetch (same flow as first activation)
```

## Files Changed

| File | Change |
|------|--------|
| `src/web/technical-analysis.html` | Add tab button markup |
| `src/web/technical-analysis.js` | Tab state management, Universe lazy-load, composite_score display |
| `src/web/technical-analysis-helpers.js` | Add composite_score to card rendering |
| `src/web/scanner.js` | Persist `_state.params` to localStorage on scan run |

## Out of Scope

- Scanner filter controls on the Technical Analysis page (use the Scanner page to configure and run)
- Filter funnel summary on the Universe tab
- Any backend / API changes
