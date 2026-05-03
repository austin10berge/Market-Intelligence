# Technical Analysis — Universe Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Watchlist | Universe" tab toggle to the Technical Analysis page, persist CSP scanner params across page reloads, and display `composite_score` on every candidate card.

**Architecture:** Three independent concerns are addressed in sequence — (1) scanner.js saves its params to localStorage on every change and restores them on load; (2) the Technical Analysis page gains tab state + a lazy-loading Universe data path that reads those saved params; (3) `composite_score` is surfaced in the card UI (already returned by both endpoints). No backend changes.

**Tech Stack:** Vanilla JS (ES modules), localStorage, `GET /api/screener/csp-scan` with query params, existing CSS custom properties.

---

## File Map

| File | What changes |
|------|-------------|
| `src/web/scanner.js` | Restore params from localStorage on load; save on every param/condition change |
| `src/web/technical-analysis-helpers.js` | Export `SCANNER_PARAMS_STORAGE_KEY` and `DEFAULT_SCANNER_PARAMS`; add `buildScanQueryString()` |
| `src/web/technical-analysis.html` | Tab markup above controls card; Universe state section with Refresh button |
| `src/web/technical-analysis.js` | Tab state, Universe lazy-load, composite_score in card |
| `src/web/index.css` | Tab button styles (`.ta-tab`, `.ta-tab.active`) |

---

## Task 1: Export scanner defaults and storage key from helpers

**Files:**
- Modify: `src/web/technical-analysis-helpers.js`

The Technical Analysis page needs to know the scanner's localStorage key and defaults (so it can fall back gracefully when the scanner has never been run). It also needs a function to build the query string from stored params.

Note: `scanner.js` uses internal key names (`rsi_max`, `adx_min`, `adx_max`, `dte_min`, `dte_max`) that differ from the API query param names (`max_rsi`, `min_adx`, `max_adx`, `min_dte`, `max_dte`). The mapping lives in `_buildQueryString()` in scanner.js and must be replicated in `buildScanQueryString()` below.

- [ ] **Step 1: Add exports to `technical-analysis-helpers.js`**

Append to the end of `src/web/technical-analysis-helpers.js`:

```js
export const SCANNER_PARAMS_STORAGE_KEY = "market-intelligence:csp-scanner-params";

export const DEFAULT_SCANNER_PARAMS = {
    min_cap:   10,
    max_price: 150,
    min_beta:  0.8,
    max_beta:  2.4,
    min_vol:   30,
    rsi_max:   50,
    adx_min:   15,
    adx_max:   50,
    dte_min:   3,
    dte_max:   46,
    conditions: [],
};

export function loadScannerParams() {
    try {
        const raw = window.localStorage.getItem(SCANNER_PARAMS_STORAGE_KEY);
        if (!raw) return { ...DEFAULT_SCANNER_PARAMS, conditions: [] };
        const parsed = JSON.parse(raw);
        return {
            min_cap:   typeof parsed.min_cap   === 'number' ? parsed.min_cap   : DEFAULT_SCANNER_PARAMS.min_cap,
            max_price: typeof parsed.max_price === 'number' ? parsed.max_price : DEFAULT_SCANNER_PARAMS.max_price,
            min_beta:  typeof parsed.min_beta  === 'number' ? parsed.min_beta  : DEFAULT_SCANNER_PARAMS.min_beta,
            max_beta:  typeof parsed.max_beta  === 'number' ? parsed.max_beta  : DEFAULT_SCANNER_PARAMS.max_beta,
            min_vol:   typeof parsed.min_vol   === 'number' ? parsed.min_vol   : DEFAULT_SCANNER_PARAMS.min_vol,
            rsi_max:   typeof parsed.rsi_max   === 'number' ? parsed.rsi_max   : DEFAULT_SCANNER_PARAMS.rsi_max,
            adx_min:   typeof parsed.adx_min   === 'number' ? parsed.adx_min   : DEFAULT_SCANNER_PARAMS.adx_min,
            adx_max:   typeof parsed.adx_max   === 'number' ? parsed.adx_max   : DEFAULT_SCANNER_PARAMS.adx_max,
            dte_min:   typeof parsed.dte_min   === 'number' ? parsed.dte_min   : DEFAULT_SCANNER_PARAMS.dte_min,
            dte_max:   typeof parsed.dte_max   === 'number' ? parsed.dte_max   : DEFAULT_SCANNER_PARAMS.dte_max,
            conditions: Array.isArray(parsed.conditions) ? parsed.conditions : [],
        };
    } catch {
        return { ...DEFAULT_SCANNER_PARAMS, conditions: [] };
    }
}

export function buildScanQueryString(params) {
    const qs = new URLSearchParams({
        min_cap:   params.min_cap,
        max_price: params.max_price,
        min_beta:  params.min_beta,
        max_beta:  params.max_beta,
        min_vol:   params.min_vol,
        max_rsi:   params.rsi_max,
        min_adx:   params.adx_min,
        max_adx:   params.adx_max,
        min_dte:   params.dte_min,
        max_dte:   params.dte_max,
    });
    if (params.conditions.length) {
        qs.set('conditions', params.conditions.join(','));
    }
    return qs.toString();
}
```

- [ ] **Step 2: Verify the file is valid by opening `scanner.html` in browser dev tools**

Open the browser console on `/technical-analysis.html`. No import errors should appear. (The new exports are not yet imported anywhere — that's fine.)

- [ ] **Step 3: Commit**

```bash
git add src/web/technical-analysis-helpers.js
git commit -m "feat(web): export scanner params helpers from technical-analysis-helpers"
```

---

## Task 2: Persist scanner params in scanner.js

**Files:**
- Modify: `src/web/scanner.js`

Scanner params currently live in `_state.params` (in-memory only). We need to (a) restore from localStorage on page load before rendering, and (b) write to localStorage after every change.

Three places mutate `_state.params`:
- `commitParamEdit(key)` — numeric filter edited via the badge UI
- `toggleCondition(id, checked)` — condition checkbox toggled
- `removeCondition(id)` — condition chip removed

- [ ] **Step 1: Add the storage key constant and a `_persistParams()` helper near the top of scanner.js**

Find the `// ── Scanner state` comment block (around line 19) and add above it:

```js
const SCANNER_PARAMS_KEY = 'market-intelligence:csp-scanner-params';

function _persistParams() {
    try {
        window.localStorage.setItem(SCANNER_PARAMS_KEY, JSON.stringify(_state.params));
    } catch { /* storage unavailable — ignore */ }
}
```

- [ ] **Step 2: Add a `_restoreParams()` function and call it at the top of the `DOMContentLoaded` handler**

Add the function after `_persistParams()`:

```js
function _restoreParams() {
    try {
        const raw = window.localStorage.getItem(SCANNER_PARAMS_KEY);
        if (!raw) return;
        const saved = JSON.parse(raw);
        const p = _state.params;
        if (typeof saved.min_cap   === 'number') p.min_cap   = saved.min_cap;
        if (typeof saved.max_price === 'number') p.max_price = saved.max_price;
        if (typeof saved.min_beta  === 'number') p.min_beta  = saved.min_beta;
        if (typeof saved.max_beta  === 'number') p.max_beta  = saved.max_beta;
        if (typeof saved.min_vol   === 'number') p.min_vol   = saved.min_vol;
        if (typeof saved.rsi_max   === 'number') p.rsi_max   = saved.rsi_max;
        if (typeof saved.adx_min   === 'number') p.adx_min   = saved.adx_min;
        if (typeof saved.adx_max   === 'number') p.adx_max   = saved.adx_max;
        if (typeof saved.dte_min   === 'number') p.dte_min   = saved.dte_min;
        if (typeof saved.dte_max   === 'number') p.dte_max   = saved.dte_max;
        if (Array.isArray(saved.conditions)) p.conditions = saved.conditions;
    } catch { /* corrupt storage — use defaults */ }
}
```

Then update the `DOMContentLoaded` listener (currently at line 74) to call `_restoreParams()` first:

```js
document.addEventListener('DOMContentLoaded', () => {
    _restoreParams();
    renderParamBadges();
    loadAvailableConditions().then(() => renderConditionPicker());
    loadDataFreshness();
});
```

> `renderConditionPicker()` reads `_state.params.conditions` to set checkbox state, so restoring before it renders is correct.

- [ ] **Step 3: Call `_persistParams()` at the end of `commitParamEdit()`**

Find `commitParamEdit(key)` (around line 152). After `renderParamBadges();`, add:

```js
    _persistParams();
```

Full function after edit:
```js
function commitParamEdit(key) {
    const input = document.getElementById(`param-input-${key}`);
    if (!input) return;

    const cfg = PARAM_CONFIG.find(c => c.key === key);
    let val = parseFloat(input.value);

    if (isNaN(val)) val = _state.params[key];
    val = Math.max(cfg.min, Math.min(cfg.max, val));
    _state.params[key] = val;

    renderParamBadges();
    _persistParams();
}
```

- [ ] **Step 4: Call `_persistParams()` at the end of `toggleCondition()`**

Find `toggleCondition(id, checked)` (around line 209). After `renderConditionChips();`, add:

```js
    _persistParams();
```

Full function after edit:
```js
function toggleCondition(id, checked) {
    if (checked && !_state.params.conditions.includes(id)) {
        _state.params.conditions.push(id);
    } else if (!checked) {
        _state.params.conditions = _state.params.conditions.filter(c => c !== id);
    }
    const item = document.getElementById(`cp-item-${id}`);
    if (item) item.classList.toggle('active', checked);
    renderConditionChips();
    _persistParams();
}
```

- [ ] **Step 5: Call `_persistParams()` at the end of `removeCondition()`**

Find `removeCondition(id)` (around line 221). After `renderConditionChips();`, add:

```js
    _persistParams();
```

Full function after edit:
```js
function removeCondition(id) {
    _state.params.conditions = _state.params.conditions.filter(c => c !== id);
    const item = document.getElementById(`cp-item-${id}`);
    if (item) {
        item.classList.remove('active');
        const cb = item.querySelector('input[type=checkbox]');
        if (cb) cb.checked = false;
    }
    renderConditionChips();
    _persistParams();
}
```

- [ ] **Step 6: Verify in browser**

1. Open `/scanner.html`, change the Cap filter to `15` and enable one condition checkbox.
2. Open DevTools → Application → Local Storage → check that `market-intelligence:csp-scanner-params` contains `"min_cap":15` and the condition ID.
3. Hard-reload the page. The badge should show `Cap > 15B` and the condition chip should still be selected.

- [ ] **Step 7: Commit**

```bash
git add src/web/scanner.js
git commit -m "feat(web): persist scanner params to localStorage across page reloads"
```

---

## Task 3: Add tab styles to index.css

**Files:**
- Modify: `src/web/index.css`

Add minimal tab button styles. Find the `.ta-controls-card` rule (around line 774) and insert before it:

- [ ] **Step 1: Add CSS**

```css
/* ── Technical Analysis — source tabs ──────────────────────────────────────── */
.ta-tabs {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
}

.ta-tab {
    padding: 0.45rem 1.1rem;
    border-radius: 9999px;
    border: 1px solid rgba(255, 255, 255, 0.12);
    background: rgba(15, 23, 42, 0.45);
    color: var(--text-secondary);
    font-size: 0.85rem;
    font-weight: 500;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.ta-tab:hover {
    color: var(--text-primary);
    border-color: rgba(255, 255, 255, 0.25);
}

.ta-tab.active {
    color: var(--accent-blue);
    border-color: var(--accent-blue);
    background: rgba(59, 130, 246, 0.08);
}
```

- [ ] **Step 2: Commit**

```bash
git add src/web/index.css
git commit -m "feat(web): add tab button styles for Technical Analysis page"
```

---

## Task 4: Add tab markup to technical-analysis.html

**Files:**
- Modify: `src/web/technical-analysis.html`

- [ ] **Step 1: Add tab nav and universe state section**

Replace the `<main>` block content with:

```html
        <main>
            <div class="ta-tabs">
                <button class="ta-tab active" id="tab-watchlist" onclick="switchTab('watchlist')">Watchlist</button>
                <button class="ta-tab" id="tab-universe" onclick="switchTab('universe')">Universe</button>
            </div>

            <section class="glass card ta-controls-card">
                <div class="card-header">
                    <h2>Indicators</h2>
                </div>
                <form id="indicator-controls" class="ta-controls-grid"></form>
            </section>

            <section id="technical-analysis-state" class="glass card full-width">
                <div id="technical-analysis-message" class="subtitle">Loading CSP candidates...</div>
                <button id="btn-universe-refresh" style="display:none;margin-top:0.75rem;" class="btn-scan" onclick="refreshUniverse()">Refresh Universe</button>
            </section>

            <section id="technical-analysis-list" class="ta-list"></section>
        </main>
```

> Note: `switchTab` and `refreshUniverse` are global functions that will be defined in `technical-analysis.js` in the next task. They don't need to exist yet to commit the HTML.

- [ ] **Step 2: Commit**

```bash
git add src/web/technical-analysis.html
git commit -m "feat(web): add Watchlist/Universe tab markup to Technical Analysis page"
```

---

## Task 5: Wire up tabs, Universe loading, and composite_score in technical-analysis.js

**Files:**
- Modify: `src/web/technical-analysis.js`

This is the main logic task. Replace the full file with the version below. Key changes vs. the current file:

- Import `SCANNER_PARAMS_STORAGE_KEY`, `loadScannerParams`, `buildScanQueryString` from helpers
- Add `activeTab` state, `universeData` variable
- `loadIndicatorSettings` / `persistIndicatorSettings` now also read/write `activeTab` from the settings object
- `DOMContentLoaded` calls `initTabs()` before loading candidates
- New `switchTab(tab)`, `refreshUniverse()`, `loadUniverseCandidates()` functions
- `renderCandidates(source)` replaces `renderCandidates()` — accepts an array; messages differ by tab
- `composite_score` added to the `.ta-stock-grid` metrics

- [ ] **Step 1: Update imports at top of `technical-analysis.js`**

Replace the existing import block:

```js
import {
    DEFAULT_INDICATOR_SETTINGS,
    INDICATOR_STORAGE_KEY,
    sanitizeIndicatorSettings,
    buildWidgetStudies,
    buildWidgetStudyOverrides,
    loadScannerParams,
    buildScanQueryString,
} from "./technical-analysis-helpers.js";
```

- [ ] **Step 2: Update module-level state variables**

Replace the existing module-level variables (lines 9–15):

```js
const API_BASE = window.MARKET_INTELLIGENCE_CONFIG?.apiBase || "/MISSING_CONFIG_JS_SEE_CONSOLE";

let candidates = [];
let stockDataBySymbol = {};
let indicatorSettings = loadIndicatorSettings();
let widgets = [];
let activeTab = "watchlist";   // "watchlist" | "universe"
let universeData = null;       // null = not yet loaded; [] = loaded (may be empty)
```

- [ ] **Step 3: Update `loadIndicatorSettings` to restore `activeTab`**

Replace the `loadIndicatorSettings` function:

```js
function loadIndicatorSettings() {
    try {
        const raw = window.localStorage.getItem(INDICATOR_STORAGE_KEY);
        if (!raw) return DEFAULT_INDICATOR_SETTINGS;
        const parsed = JSON.parse(raw);
        if (parsed.activeTab === "universe") activeTab = "universe";
        return sanitizeIndicatorSettings(parsed);
    } catch {
        return DEFAULT_INDICATOR_SETTINGS;
    }
}
```

- [ ] **Step 4: Update `persistIndicatorSettings` to save `activeTab`**

Replace the `persistIndicatorSettings` function:

```js
function persistIndicatorSettings() {
    try {
        window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify({
            ...indicatorSettings,
            activeTab,
        }));
    } catch {
        // Preserve interactivity even if storage is unavailable.
    }
}
```

- [ ] **Step 5: Update `DOMContentLoaded` to call `initTabs()` first**

Replace the `DOMContentLoaded` listener:

```js
document.addEventListener("DOMContentLoaded", async () => {
    renderControls();
    initTabs();
    await Promise.allSettled([loadCandidates(), loadStockData()]);
});
```

- [ ] **Step 6: Add `initTabs()`, `switchTab()`, `refreshUniverse()`, `loadUniverseCandidates()`**

Insert these functions after the `persistIndicatorSettings` function and before `loadStockData`:

```js
function initTabs() {
    // Sync button states to restored activeTab
    document.getElementById("tab-watchlist").classList.toggle("active", activeTab === "watchlist");
    document.getElementById("tab-universe").classList.toggle("active", activeTab === "universe");
}

window.switchTab = async function switchTab(tab) {
    if (tab === activeTab) return;
    activeTab = tab;
    persistIndicatorSettings();

    document.getElementById("tab-watchlist").classList.toggle("active", tab === "watchlist");
    document.getElementById("tab-universe").classList.toggle("active", tab === "universe");

    const refreshBtn = document.getElementById("btn-universe-refresh");
    refreshBtn.style.display = tab === "universe" ? "" : "none";

    if (tab === "watchlist") {
        await renderCandidates(candidates);
    } else {
        if (universeData !== null) {
            await renderCandidates(universeData);
        } else {
            await loadUniverseCandidates();
        }
    }
};

window.refreshUniverse = async function refreshUniverse() {
    universeData = null;
    await loadUniverseCandidates();
};

async function loadUniverseCandidates() {
    setMessage("Loading universe candidates…");
    destroyWidgets();
    getListEl().innerHTML = "";

    try {
        const params = loadScannerParams();
        const qs = buildScanQueryString(params);
        const response = await fetch(`${API_BASE}/screener/csp-scan?${qs}`);
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const payload = await response.json();
        universeData = Array.isArray(payload.candidates) ? payload.candidates : [];
        await renderCandidates(universeData);
    } catch (error) {
        setMessage("Unable to load universe candidates.");
        getListEl().innerHTML = `
            <section class="glass card full-width">
                <p class="subtitle">${escapeHtml(error.message)}</p>
            </section>
        `;
    }
}
```

- [ ] **Step 7: Update `loadCandidates()` to pass array to `renderCandidates` and handle tab state**

Replace `loadCandidates()`:

```js
async function loadCandidates() {
    if (activeTab !== "watchlist") return;
    setMessage("Loading CSP candidates...");

    try {
        const response = await fetch(`${API_BASE}/screener/csp`);
        if (!response.ok) throw new Error("Failed to fetch CSP candidates");
        const payload = await response.json();
        candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
        if (activeTab === "watchlist") {
            await renderCandidates(candidates);
        }
    } catch (error) {
        if (activeTab !== "watchlist") return;
        setMessage("Unable to load CSP candidates.");
        getListEl().innerHTML = `
            <section class="glass card full-width">
                <p class="subtitle">${escapeHtml(error.message)}</p>
            </section>
        `;
    }
}
```

- [ ] **Step 8: Update `DOMContentLoaded` to handle Universe tab on initial load**

The `DOMContentLoaded` handler already calls `loadCandidates()`, but if `activeTab` is `"universe"` on load (restored from localStorage), we should load universe data instead of watchlist. Replace `DOMContentLoaded`:

```js
document.addEventListener("DOMContentLoaded", async () => {
    renderControls();
    initTabs();

    const refreshBtn = document.getElementById("btn-universe-refresh");
    refreshBtn.style.display = activeTab === "universe" ? "" : "none";

    if (activeTab === "universe") {
        await Promise.allSettled([loadUniverseCandidates(), loadStockData()]);
    } else {
        await Promise.allSettled([loadCandidates(), loadStockData()]);
    }
});
```

- [ ] **Step 9: Update `renderCandidates()` to accept a source array and update message**

Replace the existing `renderCandidates()` function:

```js
async function renderCandidates(source) {
    destroyWidgets();

    const tickerCandidates = dedupeCandidatesBySymbol(source);

    if (!tickerCandidates.length) {
        const label = activeTab === "universe" ? "universe" : "CSP";
        setMessage(`No viable ${label} candidates found.`);
        getListEl().innerHTML = "";
        return;
    }

    const label = activeTab === "universe" ? "universe" : "CSP";
    setMessage(`${tickerCandidates.length} tickers loaded from ${source.length} ${label} candidates.`);
    getListEl().innerHTML = tickerCandidates.map((candidate, index) => {
        const stockMetrics = resolveStockMetrics(candidate);
        const score = Number.isFinite(candidate.composite_score)
            ? candidate.composite_score.toFixed(1)
            : "—";
        return `
        <section class="glass card full-width ta-ticker-card" data-symbol="${escapeHtml(candidate.symbol || "")}">
            <div class="card-header">
                <div>
                    <h2>${escapeHtml(candidate.symbol || "Unknown")}</h2>
                    <p class="subtitle">${buildContractSummary(candidate)}</p>
                </div>
            </div>
            <div class="ta-stock-grid">
                ${metricCell("Score", score)}
                ${metricCell("RSI", formatMetric(candidate.rsi))}
                ${metricCell("ADX", formatMetric(candidate.adx))}
                ${metricCell("IV/RV20", formatMetric(stockMetrics.atm_iv_rv20))}
                ${metricCell("IV PCT", formatPercent(stockMetrics.iv_percentile))}
                ${metricCell("P/E", formatMetric(stockMetrics.pe))}
                ${metricCell("P/FCF", formatMetric(candidate.p_free_cash_flow))}
            </div>
            <div class="ta-summary-grid">
                ${metricCell("Stock", formatMoney(candidate.current_price))}
                ${metricCell("Premium", formatMoney(candidate.premium))}
                ${metricCell("ROC", formatPercent(candidate.roc_percent))}
                ${metricCell("Ann. Yield", formatPercent(candidate.annualized_roc))}
                ${metricCell("% OTM", formatPercent(candidate.otm_percent))}
                ${metricCell("Spread", formatPercent(candidate.spread_pct))}
                ${metricCell("IV", formatPercent(candidate.impliedVolatility))}
                ${metricCell("Volume", formatInteger(candidate.volume))}
                ${metricCell("DTE", formatPlain(candidate.dte))}
            </div>
            <div id="ta-chart-${index}" class="ta-chart-shell"></div>
        </section>
    `;
    }).join("");

    for (const [index, candidate] of tickerCandidates.entries()) {
        await renderTradingViewWidget(candidate, index);
    }
}
```

- [ ] **Step 10: Update `handleControlsChanged` to pass current source to `renderCandidates`**

Replace `handleControlsChanged`:

```js
async function handleControlsChanged() {
    const controlsEl = getControlsEl();
    const next = {
        sma: indicatorSettings.sma.map((item, index) => ({
            enabled: controlsEl.querySelector(`[data-kind="sma-enabled"][data-index="${index}"]`).checked,
            length: controlsEl.querySelector(`[data-kind="sma-length"][data-index="${index}"]`).value,
        })),
        bollinger: {
            enabled: controlsEl.querySelector("[data-kind='bb-enabled']").checked,
            length: controlsEl.querySelector("[data-kind='bb-length']").value,
            multiplier: controlsEl.querySelector("[data-kind='bb-multiplier']").value,
        },
        interval: indicatorSettings.interval,
        theme: indicatorSettings.theme,
    };

    indicatorSettings = sanitizeIndicatorSettings(next);
    persistIndicatorSettings();
    renderControls();
    const source = activeTab === "universe" ? (universeData ?? []) : candidates;
    await renderCandidates(source);
}
```

- [ ] **Step 11: Verify in browser — Watchlist tab**

1. Open `/technical-analysis.html`.
2. Two tab buttons should appear above the Indicators card — "Watchlist" active by default.
3. Candidate cards should appear as before, now with a "Score" metric cell.
4. Toggling SMAs/Bollinger still re-renders correctly.

- [ ] **Step 12: Verify in browser — Universe tab**

1. Click "Universe". A spinner/loading message should appear while fetching.
2. After load, candidate cards should appear (sourced from the scanner).
3. "Score" should be populated on universe cards.
4. Click "Refresh Universe" — cards should reload.
5. Switch back to "Watchlist" — watchlist cards immediately re-render (no re-fetch).
6. Hard-reload with Universe tab active — page opens on Universe tab and fetches universe data.

- [ ] **Step 13: Commit**

```bash
git add src/web/technical-analysis.js
git commit -m "feat(web): add Universe tab with lazy-load, composite_score display, and tab persistence"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec requirement | Task |
|---|---|
| Tab UI — Watchlist \| Universe above indicator panel | Task 4 |
| Active tab persisted to localStorage | Task 5 (steps 3–4) |
| Scanner params persist on load and every change | Task 2 |
| Universe tab lazy-loads from `/api/screener/csp-scan` with saved params | Task 5 (step 6) |
| Universe data cached in memory; re-fetch via Refresh button | Task 5 (steps 6, 7) |
| Loading spinner + error state for Universe tab | Task 5 (step 6 — `setMessage` + error block) |
| `composite_score` displayed on all cards (both tabs) | Task 5 (step 9) |
| No filter funnel summary on Universe tab | Confirmed — `renderCandidates` renders cards only |
| `dedupeCandidatesBySymbol` runs on universe results | Confirmed — `renderCandidates(source)` calls it |

All spec requirements covered.

### Placeholder Scan

No TBDs, TODOs, or vague steps. All code shown in full.

### Type Consistency

- `renderCandidates(source)` — `source` is `CandidateObject[]` in all call sites (Tasks 5 steps 7, 8, 10)
- `loadScannerParams()` → `buildScanQueryString(params)` — same object shape; defined together in Task 1
- `activeTab` — always `"watchlist"` or `"universe"` string literal
- `universeData` — `null` (not loaded) or `CandidateObject[]` (loaded); guarded with `!== null` check before use
- `switchTab` and `refreshUniverse` assigned to `window.*` so they work as `onclick` attributes in HTML
