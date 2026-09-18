# Sector / Themes Toggle — Overview Tab

**Date:** 2026-06-15

## Summary

Expand the Sector overview card to full width on the Overview tab, add a pill toggle to switch between Sector ETFs and custom Themes, and extend the return columns from 1D + 1W to 1D + 1W + 1M.

---

## Layout

The Sectors card is moved out of the 2-column `overview-grid` and placed above it as a standalone full-width card — consistent with Market Posture and AI Synthesis. The `overview-grid` keeps VIX, GEX, and Breadth in their existing 2-column layout below.

---

## Toggle

A segmented pill control sits in the card header, right-aligned:

```
[ Sector ETFs ]  [ Themes ]
```

- Default: **Sector ETFs**
- Active segment: white/accent background, readable text
- Inactive segment: muted background/text
- State is ephemeral (session only — no localStorage)

---

## Column Structure (both views)

Both Sector ETFs and Themes share the same 7-column grid layout:

| Name | bar | 1D% | bar | 1W% | bar | 1M% |
|------|-----|-----|-----|-----|-----|-----|

- **Name**: flexible width, left-aligned
- Each timeframe: a proportional horizontal bar + a right-aligned monospaced percentage
- Bars scale to the max absolute value within the current visible dataset
- Rows sorted by 1D return descending; null values sort to the bottom (same behavior as current sectors card)
- Header row: blank | "1D" (spans bar+pct) | "1W" (spans bar+pct) | "1M" (spans bar+pct)
- Positive: green; Negative: red; Null: neutral/muted

---

## Sector ETFs View

Same as the existing `renderSectors()` but with the 1M column added. Data source: `cachedOverviewData.sectors` — already has `pct_1m` per ticker.

---

## Themes View

Data source: `cachedOverviewData.themes` which contains:
- `singles`: `{ label → { ticker, pct_1d, pct_1w, pct_1m } }` — single ETF themes (Semis/Memory, Nuclear, Uranium, SaaS, Fintech, Biotech, Defense, Consumer Retail)
- `baskets`: `{ label → { avg_1d, avg_1w, avg_1m, tickers: { ticker → { pct_1d, pct_1w, pct_1m } } } }` — named stock baskets (Hyperscalers, Cybersecurity, Crypto Proxy)

All singles and baskets are merged into one list and sorted by their 1D return descending (singles use `pct_1d`; baskets use `avg_1d`). Null values sort to the bottom.

### Single-ticker ETF row
- Name label (e.g., "Semis/Memory") with the ETF ticker in muted text below at 10px (e.g., "SMH")
- 1D / 1W / 1M bars and percentages from `pct_1d`, `pct_1w`, `pct_1m`
- No expand control

### Basket row
- Small ▶ chevron at the far left of the name cell; rotates to ▼ when expanded
- Name label (e.g., "Cybersecurity") — no sub-label needed
- Returns from `avg_1d`, `avg_1w`, `avg_1m`
- Click anywhere on the row toggles expansion
- Expanded state: indented constituent rows below (one per ticker), same 7-column grid, smaller font (11px), slight background tint to visually group them

---

## Implementation Scope

**`src/web/v2/app.js`**
1. `renderOverviewView()` — move sectors card outside `overview-grid`; add pill toggle HTML inside card header; wire toggle click to switch between `renderSectors()` and `renderThemes()`
2. `renderSectors()` — add 1M column (bar + pct) to header and each data row
3. New `renderThemes()` — builds combined singles + baskets list with expandable basket rows

**`src/web/v2/index.html` (CSS)**
1. Pill toggle styles: segmented control in card header
2. Basket expansion styles: chevron rotation, indented sub-rows, tint background
3. Update `.sector-bar-row` grid template from 5 columns to 7 columns (add 1M bar + pct)

**No backend changes required.** The `/api/market-overview` endpoint already returns `themes` with all needed data (`pct_1m` for sectors is also already present).

---

## Out of Scope

- Persisting toggle state across sessions
- Filtering or reordering themes
- Adding new themes or editing basket composition
