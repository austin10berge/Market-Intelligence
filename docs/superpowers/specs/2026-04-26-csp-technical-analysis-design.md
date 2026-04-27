# CSP Technical Analysis Page Design

## Goal

Add a `Technical Analysis` entry point to the CSP screener dashboard that opens a dedicated page showing TradingView charts for each CSP candidate in screener order, with a compact CSP summary above each chart and shared indicator controls that persist across page reloads and service restarts.

## Current Context

- The dashboard frontend is a static site under `src/web`.
- The CSP screener is rendered by `src/web/index.html` and `src/web/app.js`.
- CSP candidates are already fetched from `GET /api/screener/csp`.
- The existing frontend uses plain HTML, CSS, and vanilla JavaScript rather than a client-side router or framework.
- The current UI already has a `backtest.html` page and a `watchlist.html` page, so a new standalone `technical-analysis.html` page fits the existing navigation model.

## User-Approved Product Decisions

- Add a `Technical Analysis` button at the top of the `Cash Secured Puts (CSP)` section.
- Route to a separate page rather than inline expansion on the main dashboard.
- Render each ticker in the same order it appears in the CSP screener results.
- Each ticker section includes a compact CSP summary card and a chart beneath it.
- Use the same kind of TradingView charting experience as the reference page.
- Provide simple on-page global indicator controls that affect every chart on the page.
- Indicator settings must persist across page reloads and service restarts.

## External Constraint

The TradingView embeddable Advanced Chart widget supports default studies through the widget `studies` configuration. TradingView also documents browser `localStorage` persistence for user settings by default. For this feature, browser persistence is sufficient for the approved requirement, but it is explicitly browser-local rather than server-shared across devices.

## Recommended Approach

Create a dedicated `technical-analysis.html` page with its own JavaScript module that:

1. Fetches the CSP screener payload from the existing API.
2. Renders a vertical list of ticker sections in returned order.
3. Builds one TradingView Advanced Chart widget per ticker.
4. Exposes a shared indicator toolbar at the top of the page.
5. Saves indicator preferences in browser `localStorage`.
6. Rebuilds or refreshes all chart widgets when indicator settings change.

This approach best matches the requested watchlist-style experience, keeps the implementation aligned with the current static-site architecture, and avoids introducing a front-end framework or server-side persistence layer that the feature does not require.

## Information Architecture

### Dashboard entry point

The existing CSP card header on `index.html` gains a prominent `Technical Analysis` button. It should live in the header area so the action is visible before the table content. The button links directly to `technical-analysis.html`.

### Technical Analysis page layout

The new page should use the current dashboard visual language, not a separate design system. The layout is:

1. Header with page title, short subtitle, and back-navigation to the main dashboard.
2. Global indicator control bar pinned near the top of the content flow.
3. A scrollable stack of ticker sections, one section per CSP candidate.

### Per-ticker section layout

Each ticker section contains:

- A section heading with the ticker symbol and the option contract context.
- A compact CSP summary strip using existing screener fields already available from the API.
- A TradingView chart container sized large enough to resemble the reference page experience.

The section order must match the current CSP screener order exactly. The page should not apply an additional client-side sort unless a future requirement explicitly adds one.

## Data Model

### CSP data source

Reuse the existing `GET /api/screener/csp` endpoint. The page should consume the same candidate objects already used by the dashboard and should not introduce a second technical-analysis-specific backend endpoint unless blocked by missing fields.

### Summary card fields

The compact summary card should reuse fields already present in the CSP results where available:

- `symbol`
- `current_price`
- `strike`
- `premium`
- `roc_percent`
- `annualized_roc`
- `otm_percent`
- `spread_pct`
- `impliedVolatility`
- `volume`
- `dte`
- `expiration` when it already exists in the current payload

The first implementation should not add a new backend endpoint. Backend changes are allowed only if the existing CSP payload is missing a field required to render either the compact summary card or the option-contract heading.

### Indicator settings model

Persist a single global settings object in `localStorage`, for example:

```json
{
  "sma": [
    { "enabled": true, "length": 20 },
    { "enabled": true, "length": 50 },
    { "enabled": true, "length": 200 }
  ],
  "bollinger": {
    "enabled": true,
    "length": 20,
    "multiplier": 2
  },
  "interval": "D",
  "theme": "dark"
}
```

This schema should be centralized in the page script so future indicator additions only require one config update plus UI wiring in one place.

## Indicator Controls

### Scope

Controls are global to the page. A change updates every current chart and must also apply to any charts created later on that page.

### Initial control set

The first version should support:

- Toggle for each SMA line.
- Numeric input for each SMA period.
- Toggle for Bollinger Bands.
- Numeric input for Bollinger length.
- Numeric input for Bollinger multiplier.

Optional but acceptable if it falls out naturally from TradingView configuration:

- Default interval selector such as `1D`, `4H`, `1W`.

### Behavior

- On page load, read saved settings from `localStorage`.
- If no saved settings exist, use sensible defaults.
- When the user updates controls, validate values, save them immediately, and refresh all chart widgets with the new studies configuration.
- New charts created after a settings change must read from the same shared settings source.

## TradingView Integration Strategy

Use TradingView’s embeddable Advanced Chart widget rather than building a chart stack from scratch.

### Why this is the right fit

- It is visually closest to the requested reference page.
- It natively supports technical studies.
- It avoids custom OHLC rendering and overlay math in the frontend.
- It fits the project’s existing static-page architecture.

### Symbol mapping

The page should convert each ticker into the symbol format expected by TradingView. The initial implementation can assume U.S. equities and ETFs and map to a default exchange prefix such as `NASDAQ:` or `NYSE:` only if your existing symbols require it. A better implementation is to start with the raw symbol when accepted by the widget and only add mapping logic if real symbols fail to resolve.

### Study generation

The page script should own a function that converts the saved settings object into the TradingView `studies` array. This function is the primary extension point for future indicator changes.

If the widget configuration alone is insufficient to express custom study parameters dynamically, the implementation may recreate chart widgets on settings change rather than mutate existing widgets in place. Re-creation is acceptable because the page is a read-focused technical-analysis view, not a low-latency trading screen.

## Error Handling

- If the CSP API fetch fails, show a clear full-page error state with retry guidance.
- If the screener returns zero candidates, show an empty state rather than blank chart placeholders.
- If an individual TradingView chart fails to render, keep the summary card visible and replace the chart with an inline error panel for that ticker.
- If `localStorage` read or write fails, fall back to in-memory defaults so the page remains usable.

## Responsiveness

- Desktop: large stacked charts with the summary metrics compressed into a single row or compact grid.
- Tablet/mobile: summary cards collapse into a wrapped grid, charts remain full width, and control inputs stack cleanly.
- The global control bar must remain usable on narrow screens without horizontal overflow where practical.

## Accessibility and UX

- Buttons and inputs need visible labels.
- The page should provide a clear way back to the dashboard.
- Control changes should not require a separate save button unless implementation complexity forces it; immediate-save is preferred.
- The page should avoid surprising reordering. Screener order is stable for the duration of a render cycle.

## Files Likely In Scope

- `src/web/index.html`
- `src/web/index.css`
- `src/web/app.js`
- `src/web/technical-analysis.html`
- `src/web/technical-analysis.js`

Backend scope is conditional:

- `src/api/main.py`

## Testing Strategy

### Functional checks

- The dashboard CSP section shows a visible `Technical Analysis` button.
- Clicking it opens the new page.
- The new page fetches CSP candidates and renders them in the same order as the dashboard screener data.
- Each ticker section shows both summary metrics and a chart.
- Changing global indicator controls updates all charts.
- Reloading the page preserves the indicator settings.
- Restarting the service does not clear browser-saved settings.

### Regression checks

- Existing dashboard CSP rendering still works.
- Existing watchlist and backtester pages still load.
- Mobile layout remains usable.

## Out of Scope

- Server-side user preference storage.
- Cross-device synchronization of indicator settings.
- User-specific authentication.
- Per-chart custom indicator settings separate from the global page controls.
- Replacing TradingView with a custom charting engine.

## Open Assumptions Resolved In This Design

- Persistence means browser persistence on the same device/browser, which survives reloads and service restarts.
- A standalone page is preferable to an in-dashboard expansion.
- The visual format should resemble the reference site’s stacked watchlist but stay within the current dashboard styling.
