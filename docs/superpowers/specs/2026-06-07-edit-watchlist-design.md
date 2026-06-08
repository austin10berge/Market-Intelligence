# v2 Edit Watchlist — Design Spec

**Date:** 2026-06-07
**Branch:** redesign/ui-refresh
**Status:** Approved

---

## Overview

A native v2 SPA settings surface for editing all watchlists and CSP screener parameters. Replaces `watchlist.html` (v1) as the primary editing UI. Accessed via a pencil icon in the Watchlist view header; renders as a full main-content sub-view within the existing SPA shell.

---

## Entry Point

- A pencil icon button (`✎`) is added to the right side of the Watchlist section header, next to the cache badge.
- Tapping it calls `renderEditWatchlistView()`, which replaces `#main-content` (same pattern as `switchTab`).
- The bottom nav remains visible with Watchlist still active — this is a sub-view, not a top-level tab.
- A `←` back button in the edit view header restores the watchlist view, re-entering whichever sub-tab (`tickers`/`csp`/`leaps`) was last active.
- Header title: "Edit Watchlist", styled with `.section-title` (uppercase monospace muted label).

---

## Structure

```
renderEditWatchlistView()
├── Header: ← back | "EDIT WATCHLIST" title
├── Sub-tabs: Watchlists | Channels | Settings
└── Content pane (switches on active sub-tab)
    ├── renderEditWatchlistsTab()
    ├── renderEditChannelsTab()
    └── renderEditSettingsTab()
```

State: `activeEditTab` string (`'watchlists' | 'channels' | 'settings'`). Resets to `'watchlists'` each time `renderEditWatchlistView()` is called (no memory between visits).

---

## Watchlists Tab

Two stacked card sections, one for Stock Watchlist and one for CSP Watchlist.

### Chip Tag Editor (shared component)

Each watchlist uses a chip tag editor:

- **Container**: flex-wrap div inside an `.overview-card`-style container.
- **Chips**: each saved ticker renders as a pill using `.scn-chip` style (`rgba(41,98,255,0.1)` background, blue border, IBM Plex Mono 13px). Each chip has a `×` button (`color: --tv-muted`, hover `--tv-text`) that removes it.
- **Add input**: an inline borderless input that flows after the last chip. Auto-sized (`min-width: 60px`, expands with content). Uppercase-transforms all input. Pressing `Enter` or `,` trims, uppercases, deduplicates, and mints a new chip. `Escape` clears the draft. Empty input on Enter is a no-op.
- **Save button**: per-section, full-width, styled as `.scn-sheet-apply` (blue, IBM Plex Mono). On save: POSTs to the relevant API, disables button during request, shows inline status message (`✓ Saved` green / `✗ Failed` red) that auto-clears after 4s.

### API calls

| Section | GET | POST | Side effect |
|---|---|---|---|
| Stock Watchlist | `GET /api/watchlist/stock` | `POST /api/watchlist/stock` | Cache invalidated |
| CSP Watchlist | `GET /api/watchlist` | `POST /api/watchlist` | Cache invalidated + background re-scan |

Both watchlists are loaded on first render of the Watchlists tab (parallel fetches). Tickers are uppercased and deduplicated before save.

---

## Channels Tab

Manages the YouTube channel URL list.

### Chip Tag Editor (URL variant)

- **Display label**: channel handle extracted from URL (e.g. `@SomeChannel` from `youtube.com/@SomeChannel`). Falls back to the raw URL if parsing fails.
- **Stored value**: full URL string.
- **Add input**: accepts a full URL. On `Enter`, validates that the string contains `youtube.com/` — invalid input triggers a CSS `@keyframes shake` animation on the input field without adding a chip.
- One **Save** button for the whole list (`POST /api/youtube-channels`).
- Loaded from `GET /api/youtube-channels` on first render of the Channels tab.

---

## Settings Tab (CSP Screener Parameters)

A scrollable form matching the v1 CSP settings section, restyled with v2 components.

### Sections

Three sub-section titles using `.scn-sheet-section-title` (uppercase mono, muted, 11px).

**CONTRACT FILTERS**
Fields (label left, input right using `.scn-field` / `.scn-field-input`):
- Min DTE / Max DTE
- Min Delta / Max Delta
- Min Capital ROC %
- Max Bid/Ask Spread %

**TECHNICAL FILTERS**
- Min IV %
- Min RSI / Max RSI
- Min ADX / Max ADX
- Pullback Mode (toggle — reuses existing v1 toggle CSS adapted to `--tv-*` variables)

**SCORE WEIGHTS**
- Annualized Yield weight
- PoP Proxy weight
- IV Percentile weight
- RSI Quality weight
- ADX Trend weight

Below the five weight inputs: a live **weight-sum badge** (monospace, small). Precedence top-down:
1. Green (`--tv-green`) when `|sum − 1.00| ≤ 0.001` — valid, save enabled
2. Yellow (`--tv-yellow`) when `0.95 ≤ sum ≤ 1.05` but outside ±0.001 — close, save disabled
3. Red (`--tv-red`) otherwise — invalid, save disabled

### Save behavior

- Full-width "SAVE & RE-SCAN" button (`.scn-sheet-apply` blue). Disabled when weight sum is invalid.
- POSTs to `POST /api/settings/csp` with all field values.
- Inline status feedback same as other sections.
- Loaded from `GET /api/settings/csp` on first render of the Settings tab.

---

## Styling Rules

All new elements use existing CSS variables and component classes from `src/web/v2/index.html`:

| Token | Usage |
|---|---|
| `--tv-bg`, `--tv-surface`, `--tv-surface2` | Backgrounds |
| `--tv-border` | Borders, chip borders |
| `--tv-text`, `--tv-muted` | Labels and secondary text |
| `--tv-blue` / `rgba(41,98,255,…)` | Active chips, save buttons |
| `--tv-green`, `--tv-red`, `--tv-yellow` | Status feedback, weight sum |
| IBM Plex Mono | All chip text, inputs, labels, buttons |
| IBM Plex Sans Condensed | Body copy / field descriptions |

Reused component classes: `.sub-tab`, `.sub-tab.active`, `.overview-card`, `.overview-card-title`, `.scn-chip`, `.scn-field`, `.scn-field-input`, `.scn-sheet-apply`, `.scn-sheet-section-title`.

New classes needed: `.edit-chip-editor`, `.edit-chip`, `.edit-chip-remove`, `.edit-chip-input`, `.edit-weight-sum-badge`.

---

## Implementation Scope

All changes are confined to `src/web/v2/app.js` and `src/web/v2/index.html`. No new files, no backend changes, no dependency additions.

New JS functions:
- `renderEditWatchlistView()` — entry point, renders header + sub-tabs
- `renderEditSubTabs()` — sub-tab bar
- `switchEditTab(tab)` — swap content pane
- `renderEditWatchlistsTab()` — parallel-fetches and renders two chip editors
- `renderEditChannelsTab()` — fetches and renders channel chip editor
- `renderEditSettingsTab()` — fetches CSP settings and renders form
- `makeChipEditor(containerId, tickers, onSave, opts)` — reusable chip editor factory (handles add/remove/save, URL-mode flag)
- `saveWatchlist(type)` — POSTs stock or CSP watchlist
- `saveChannels()` — POSTs YouTube channels
- `saveCspSettings()` — validates weights, POSTs CSP settings

New CSS: chip editor styles, weight sum badge, shake animation, edit view header. Added to the `<style>` block in `index.html`.

---

## Out of Scope

- No change to the bottom nav (remains 4 items)
- No change to v1 `watchlist.html` (left as-is for now)
- No LEAPS settings or stock screener settings (not in v1 either)
- No drag-to-reorder on chips
