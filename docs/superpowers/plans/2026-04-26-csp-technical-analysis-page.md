# CSP Technical Analysis Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `Technical Analysis` button to the CSP dashboard and a new standalone page that renders each CSP ticker in screener order with a compact summary card, TradingView charts, and shared persistent indicator controls.

**Architecture:** Keep the feature frontend-only inside `src/web`. Add one new HTML page, one page controller script, and one small pure helper module for indicator defaults, localStorage persistence, and TradingView study definitions so the chart-page logic can be tested outside the browser. Reuse the existing `GET /api/screener/csp` API and the current visual language from `index.css`.

**Tech Stack:** Static HTML, vanilla JavaScript ES modules, existing CSS, TradingView Advanced Chart widget constructor, browser `localStorage`, Node built-in test runner for helper tests.

---

## File Structure

- Modify: `src/web/index.html`
- Modify: `src/web/index.css`
- Create: `src/web/technical-analysis.html`
- Create: `src/web/technical-analysis.js`
- Create: `src/web/technical-analysis-helpers.js`
- Test: `tests/test_technical_analysis_helpers.mjs`

## Task 1: Add the dashboard entry point and page scaffold

**Files:**
- Modify: `src/web/index.html`
- Modify: `src/web/index.css`
- Create: `src/web/technical-analysis.html`
- Test: `src/web/index.html` in browser

- [ ] **Step 1: Write the failing UI expectation down as a manual acceptance test**

Create this checklist in your working notes before editing code:

```text
1. The CSP card header shows a visible "Technical Analysis" action.
2. Clicking it opens /technical-analysis.html.
3. The new page has a back link, a title, an indicator toolbar, and an empty-state/loading container.
```

- [ ] **Step 2: Add the CTA to the CSP header in `src/web/index.html`**

Update the CSP header block so the heading and the new action can coexist cleanly:

```html
<div class="card-header card-header-actions">
    <div class="card-header-copy">
        <h2>Cash Secured Puts (CSP)</h2>
    </div>
    <div class="card-header-tools">
        <a href="technical-analysis.html" class="section-link-btn">
            Technical Analysis
        </a>
        <div class="badge green">High Conviction Premium</div>
        <span id="cache-status-csp" class="cache-badge"></span>
    </div>
</div>
```

- [ ] **Step 3: Create the new page scaffold in `src/web/technical-analysis.html`**

Add a standalone page that follows the same static-page pattern as `backtest.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="CSP technical analysis charts">
    <title>CSP Technical Analysis</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="index.css">
    <script src="/config.js"></script>
    <script type="module" src="technical-analysis.js"></script>
</head>
<body>
    <div id="app" class="technical-analysis-page">
        <header class="glass ta-header">
            <div class="header-content">
                <a href="index.html" class="btn-icon ta-back-link" aria-label="Back to dashboard">&#8592;</a>
                <div>
                    <h1>CSP Technical Analysis</h1>
                    <p>Charts follow the current CSP screener order and share one indicator configuration.</p>
                </div>
            </div>
        </header>

        <main>
            <section class="glass card ta-controls-card">
                <div class="card-header">
                    <h2>Indicators</h2>
                </div>
                <form id="indicator-controls" class="ta-controls-grid"></form>
            </section>

            <section id="technical-analysis-state" class="glass card full-width">
                <div id="technical-analysis-message" class="subtitle">Loading CSP candidates...</div>
            </section>

            <section id="technical-analysis-list" class="ta-list"></section>
        </main>
    </div>
    <script src="https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js" async></script>
</body>
</html>
```

- [ ] **Step 4: Add the page-level styles to `src/web/index.css`**

Append focused styles for header actions, controls, and chart sections:

```css
.card-header-actions {
    gap: 1rem;
}

.card-header-copy,
.card-header-tools {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.card-header-tools {
    margin-left: auto;
    flex-wrap: wrap;
    justify-content: flex-end;
}

.section-link-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 2.4rem;
    padding: 0.6rem 1rem;
    border-radius: 9999px;
    text-decoration: none;
    color: var(--text-primary);
    background: rgba(59, 130, 246, 0.16);
    border: 1px solid rgba(96, 165, 250, 0.28);
}

.technical-analysis-page {
    max-width: 1480px;
}

.ta-header .header-content {
    display: flex;
    align-items: center;
    gap: 1rem;
}

.ta-controls-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 1rem;
}

.ta-list {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    margin-top: 1.5rem;
}

.ta-ticker-card {
    padding: 1.5rem;
}

.ta-summary-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 0.75rem;
    margin: 1rem 0 1.25rem;
}

.ta-chart-shell {
    min-height: 560px;
    border-radius: 12px;
    overflow: hidden;
    background: rgba(15, 23, 42, 0.55);
}
```

- [ ] **Step 5: Run a fast HTML/CSS sanity pass**

Run:

```bash
rg -n "technical-analysis|section-link-btn|card-header-actions" src/web/index.html src/web/index.css src/web/technical-analysis.html
```

Expected:

```text
Matches in all three files confirming the route, scaffold, and styling hooks were added.
```

- [ ] **Step 6: Commit the scaffold**

Run:

```bash
git add src/web/index.html src/web/index.css src/web/technical-analysis.html
git commit -m "feat: add CSP technical analysis page scaffold"
```

## Task 2: Implement indicator settings helpers and tests

**Files:**
- Create: `src/web/technical-analysis-helpers.js`
- Test: `tests/test_technical_analysis_helpers.mjs`

- [ ] **Step 1: Write the failing helper tests**

Create `tests/test_technical_analysis_helpers.mjs` with coverage for defaults, sanitization, and TradingView study-definition generation:

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {
    DEFAULT_INDICATOR_SETTINGS,
    buildStudyDefinitions,
    sanitizeIndicatorSettings,
} from "../src/web/technical-analysis-helpers.js";

test("sanitizeIndicatorSettings falls back to defaults for invalid payloads", () => {
    const result = sanitizeIndicatorSettings({
        sma: [{ enabled: true, length: -5 }],
        bollinger: { enabled: true, length: 0, multiplier: -1 },
        interval: "bad",
        theme: "alien",
    });

    assert.deepEqual(result.sma.map(item => item.length), [20, 50, 200]);
    assert.equal(result.bollinger.length, 20);
    assert.equal(result.bollinger.multiplier, 2);
    assert.equal(result.interval, "D");
    assert.equal(result.theme, "dark");
});

test("buildStudyDefinitions includes enabled SMAs and Bollinger Bands with inputs", () => {
    const studies = buildStudyDefinitions({
        sma: [
            { enabled: true, length: 21 },
            { enabled: false, length: 50 },
            { enabled: true, length: 200 },
        ],
        bollinger: { enabled: true, length: 20, multiplier: 2 },
        interval: "D",
        theme: "dark",
    });

    assert.deepEqual(studies, [
        { name: "Moving Average", inputs: { length: 21 } },
        { name: "Moving Average", inputs: { length: 200 } },
        { name: "Bollinger Bands", inputs: { length: 20, mult: 2 } },
    ]);
});
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run:

```bash
node --test tests/test_technical_analysis_helpers.mjs
```

Expected:

```text
FAIL because src/web/technical-analysis-helpers.js does not exist yet.
```

- [ ] **Step 3: Implement the helper module**

Create `src/web/technical-analysis-helpers.js` as a small pure module:

```javascript
export const INDICATOR_STORAGE_KEY = "market-intelligence:csp-technical-analysis";

export const DEFAULT_INDICATOR_SETTINGS = {
    sma: [
        { enabled: true, length: 20 },
        { enabled: true, length: 50 },
        { enabled: true, length: 200 },
    ],
    bollinger: {
        enabled: true,
        length: 20,
        multiplier: 2,
    },
    interval: "D",
    theme: "dark",
};

const ALLOWED_INTERVALS = new Set(["D", "240", "W"]);
const ALLOWED_THEMES = new Set(["dark", "light"]);

function clampInt(value, fallback, min, max) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
}

function clampFloat(value, fallback, min, max) {
    const parsed = Number.parseFloat(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
}

export function sanitizeIndicatorSettings(raw = {}) {
    const fallback = DEFAULT_INDICATOR_SETTINGS;
    const rawSma = Array.isArray(raw.sma) ? raw.sma : fallback.sma;

    return {
        sma: fallback.sma.map((item, index) => ({
            enabled: typeof rawSma[index]?.enabled === "boolean" ? rawSma[index].enabled : item.enabled,
            length: clampInt(rawSma[index]?.length, item.length, 2, 400),
        })),
        bollinger: {
            enabled: typeof raw.bollinger?.enabled === "boolean" ? raw.bollinger.enabled : fallback.bollinger.enabled,
            length: clampInt(raw.bollinger?.length, fallback.bollinger.length, 2, 400),
            multiplier: clampFloat(raw.bollinger?.multiplier, fallback.bollinger.multiplier, 0.1, 5),
        },
        interval: ALLOWED_INTERVALS.has(raw.interval) ? raw.interval : fallback.interval,
        theme: ALLOWED_THEMES.has(raw.theme) ? raw.theme : fallback.theme,
    };
}

export function buildStudyDefinitions(settings) {
    const studies = [];

    settings.sma.forEach((item) => {
        if (item.enabled) {
            studies.push({
                name: "Moving Average",
                inputs: { length: item.length },
            });
        }
    });

    if (settings.bollinger.enabled) {
        studies.push({
            name: "Bollinger Bands",
            inputs: {
                length: settings.bollinger.length,
                mult: settings.bollinger.multiplier,
            },
        });
    }

    return studies;
}
```

- [ ] **Step 4: Make the helper tests meaningful for study order and defaults**

Extend the test file with one more test for missing payloads:

```javascript
test("sanitizeIndicatorSettings returns defaults when payload is missing", () => {
    assert.deepEqual(
        sanitizeIndicatorSettings(),
        DEFAULT_INDICATOR_SETTINGS,
    );
});
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run:

```bash
node --test tests/test_technical_analysis_helpers.mjs
```

Expected:

```text
PASS all tests in tests/test_technical_analysis_helpers.mjs
```

- [ ] **Step 6: Commit the helper layer**

Run:

```bash
git add src/web/technical-analysis-helpers.js tests/test_technical_analysis_helpers.mjs
git commit -m "test: add technical analysis indicator helper coverage"
```

## Task 3: Implement page data loading, control persistence, and chart rendering

**Files:**
- Create: `src/web/technical-analysis.js`
- Modify: `src/web/technical-analysis.html`
- Modify: `src/web/index.css`
- Test: `tests/test_technical_analysis_helpers.mjs`

- [ ] **Step 1: Write down the failing runtime checks before implementation**

Use these browser checks as the failing acceptance criteria:

```text
1. Loading the page should fetch /api/screener/csp and replace the loading state.
2. Each CSP candidate should render once, in array order.
3. Editing any global control should persist settings and redraw every chart.
4. Reloading the page should restore the last-used settings.
```

- [ ] **Step 2: Implement the page controller in `src/web/technical-analysis.js`**

Create the page controller with state loading, control binding, CSP rendering, and a widget registry:

```javascript
import {
    DEFAULT_INDICATOR_SETTINGS,
    INDICATOR_STORAGE_KEY,
    buildStudyDefinitions,
    sanitizeIndicatorSettings,
} from "./technical-analysis-helpers.js";

const API_BASE = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || "/MISSING_CONFIG_JS_SEE_CONSOLE";
const listEl = document.getElementById("technical-analysis-list");
const messageEl = document.getElementById("technical-analysis-message");
const controlsEl = document.getElementById("indicator-controls");

let candidates = [];
let indicatorSettings = loadIndicatorSettings();
let widgets = [];

document.addEventListener("DOMContentLoaded", async () => {
    widgets = [];
    renderControls();
    await loadCandidates();
});

function loadIndicatorSettings() {
    try {
        const raw = window.localStorage.getItem(INDICATOR_STORAGE_KEY);
        return raw ? sanitizeIndicatorSettings(JSON.parse(raw)) : DEFAULT_INDICATOR_SETTINGS;
    } catch {
        return DEFAULT_INDICATOR_SETTINGS;
    }
}

function persistIndicatorSettings() {
    try {
        window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify(indicatorSettings));
    } catch {
        // keep page usable even if storage is unavailable
    }
}

async function loadCandidates() {
    messageEl.textContent = "Loading CSP candidates...";
    try {
        const response = await fetch(`${API_BASE}/screener/csp`);
        if (!response.ok) throw new Error("Failed to fetch CSP candidates");
        const payload = await response.json();
        candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
        renderCandidates();
    } catch (error) {
        messageEl.textContent = "Unable to load CSP candidates.";
        listEl.innerHTML = `<section class="glass card full-width"><p class="subtitle">${error.message}</p></section>`;
    }
}
```

- [ ] **Step 3: Add control rendering and change handling**

In the same file, render one global toolbar and redraw all cards when settings change:

```javascript
function renderControls() {
    controlsEl.innerHTML = `
        ${indicatorSettings.sma.map((item, index) => `
            <label class="ta-control-group">
                <span>SMA ${index + 1}</span>
                <input type="checkbox" data-kind="sma-enabled" data-index="${index}" ${item.enabled ? "checked" : ""}>
                <input type="number" min="2" max="400" data-kind="sma-length" data-index="${index}" value="${item.length}">
            </label>
        `).join("")}
        <label class="ta-control-group">
            <span>Bollinger Bands</span>
            <input type="checkbox" data-kind="bb-enabled" ${indicatorSettings.bollinger.enabled ? "checked" : ""}>
            <input type="number" min="2" max="400" data-kind="bb-length" value="${indicatorSettings.bollinger.length}">
            <input type="number" min="0.1" max="5" step="0.1" data-kind="bb-multiplier" value="${indicatorSettings.bollinger.multiplier}">
        </label>
    `;

    controlsEl.oninput = handleControlsChanged;
    controlsEl.onchange = handleControlsChanged;
}

function handleControlsChanged() {
    const next = {
        sma: indicatorSettings.sma.map((item, index) => ({
            enabled: controlsEl.querySelector(`[data-kind="sma-enabled"][data-index="${index}"]`).checked,
            length: controlsEl.querySelector(`[data-kind="sma-length"][data-index="${index}"]`).value,
        })),
        bollinger: {
            enabled: controlsEl.querySelector(`[data-kind="bb-enabled"]`).checked,
            length: controlsEl.querySelector(`[data-kind="bb-length"]`).value,
            multiplier: controlsEl.querySelector(`[data-kind="bb-multiplier"]`).value,
        },
        interval: indicatorSettings.interval,
        theme: indicatorSettings.theme,
    };

    indicatorSettings = sanitizeIndicatorSettings(next);
    persistIndicatorSettings();
    renderControls();
    destroyWidgets();
    renderCandidates();
}
```

- [ ] **Step 4: Render the ticker cards and TradingView widgets**

Finish `src/web/technical-analysis.js` with summary-card rendering and per-symbol widget creation:

```javascript
function renderCandidates() {
    if (!candidates.length) {
        messageEl.textContent = "No viable CSP candidates found.";
        listEl.innerHTML = "";
        return;
    }

    messageEl.textContent = `${candidates.length} CSP candidates loaded.`;
    listEl.innerHTML = candidates.map((candidate, index) => `
        <section class="glass card full-width ta-ticker-card" data-symbol="${candidate.symbol}">
            <div class="card-header">
                <div>
                    <h2>${candidate.symbol}</h2>
                    <p class="subtitle">${candidate.expiration} ${candidate.strike.toFixed(2)} put · ${candidate.dte} DTE</p>
                </div>
            </div>
            <div class="ta-summary-grid">
                ${metricCell("Stock", candidate.current_price?.toFixed(2))}
                ${metricCell("Premium", candidate.premium?.toFixed(2))}
                ${metricCell("ROC", `${candidate.roc_percent}%`)}
                ${metricCell("Ann. Yield", candidate.annualized_roc ? `${candidate.annualized_roc}%` : "—")}
                ${metricCell("% OTM", `${candidate.otm_percent}%`)}
                ${metricCell("Spread", candidate.spread_pct > 0 ? `${candidate.spread_pct.toFixed(1)}%` : "—")}
                ${metricCell("IV", candidate.impliedVolatility > 0 ? `${candidate.impliedVolatility.toFixed(1)}%` : "—")}
                ${metricCell("Volume", candidate.volume > 0 ? candidate.volume.toLocaleString() : "—")}
            </div>
            <div id="ta-chart-${index}" class="ta-chart-shell"></div>
        </section>
    `).join("");

    candidates.forEach((candidate, index) => renderTradingViewWidget(candidate, index));
}

function metricCell(label, value) {
    return `<div class="metric"><span class="m-val">${value ?? "—"}</span><span class="m-lbl">${label}</span></div>`;
}

function renderTradingViewWidget(candidate, index) {
    const container = document.getElementById(`ta-chart-${index}`);
    if (!container || !window.TradingView?.widget) {
        container.innerHTML = "<div class='trade-item'>Chart failed to initialize.</div>";
        return;
    }

    container.innerHTML = "";
    const widget = new window.TradingView.widget({
        autosize: true,
        symbol: candidate.symbol,
        interval: indicatorSettings.interval,
        timezone: "exchange",
        theme: indicatorSettings.theme,
        style: "1",
        locale: "en",
        backgroundColor: "rgba(11, 15, 25, 1)",
        withdateranges: true,
        hide_side_toolbar: false,
        allow_symbol_change: false,
        save_image: false,
        container_id: `ta-chart-${index}`,
        support_host: "https://www.tradingview.com",
    });

    widgets.push(widget);
    widget.onChartReady(() => {
        buildStudyDefinitions(indicatorSettings).forEach((study) => {
            widget.activeChart().createStudy(
                study.name,
                false,
                false,
                study.inputs,
            );
        });
    });
}

function destroyWidgets() {
    widgets.forEach((widget) => {
        if (typeof widget.remove === "function") {
            widget.remove();
        }
    });
    widgets = [];
}
```

- [ ] **Step 5: Add the final control styles and mobile rules**

Append the missing control styles to `src/web/index.css`:

```css
.ta-control-group {
    display: grid;
    gap: 0.45rem;
    padding: 0.85rem;
    border-radius: 12px;
    background: rgba(15, 23, 42, 0.45);
    border: 1px solid rgba(255, 255, 255, 0.06);
}

.ta-control-group input[type="number"] {
    width: 100%;
    background: rgba(15, 23, 42, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: var(--text-primary);
    border-radius: 8px;
    padding: 0.55rem 0.7rem;
}

@media (max-width: 768px) {
    .card-header-actions {
        align-items: flex-start;
        flex-direction: column;
    }

    .card-header-tools {
        margin-left: 0;
        justify-content: flex-start;
    }

    .ta-chart-shell {
        min-height: 420px;
    }
}
```

- [ ] **Step 6: Run the automated helper tests and JS syntax checks**

Run:

```bash
node --test tests/test_technical_analysis_helpers.mjs
node --check src/web/technical-analysis.js
node --check src/web/technical-analysis-helpers.js
```

Expected:

```text
PASS for the helper tests and no syntax errors for the new JS files.
```

- [ ] **Step 7: Perform manual browser verification**

Run the existing dashboard/service locally, then verify:

```text
1. Open the dashboard and click Technical Analysis from the CSP section.
2. Confirm every CSP candidate appears in the same order as the API response.
3. Change one SMA length and disable Bollinger Bands.
4. Confirm all chart cards refresh to the new indicator set and show distinct SMA periods where enabled.
5. Reload the page and confirm the same settings remain selected.
6. Restart the service, reload the page in the same browser, and confirm the settings still persist.
```

- [ ] **Step 8: Commit the feature**

Run:

```bash
git add src/web/index.css src/web/technical-analysis.js
git add src/web/technical-analysis-helpers.js src/web/technical-analysis.html
git add tests/test_technical_analysis_helpers.mjs
git commit -m "feat: add CSP technical analysis chart page"
```

## Task 4: Final regression pass and cleanup

**Files:**
- Modify: only if issues are found during verification
- Test: `src/web/index.html`, `src/web/watchlist.html`, `src/web/backtest.html`

- [ ] **Step 1: Re-run the minimal regression checks**

Run:

```text
1. Load index.html and confirm the stock, CSP, and LEAPS sections still render.
2. Load watchlist.html and confirm the form still initializes.
3. Load backtest.html and confirm the page header and controls still appear.
```

- [ ] **Step 2: If verification reveals a TradingView symbol issue, apply the smallest possible fix**

Use this fallback symbol resolver in `src/web/technical-analysis.js` only if raw symbols fail:

```javascript
function resolveTradingViewSymbol(symbol) {
    return symbol.includes(":") ? symbol : `NASDAQ:${symbol}`;
}
```

Then wire it here:

```javascript
symbol: resolveTradingViewSymbol(candidate.symbol),
```

- [ ] **Step 3: Re-run syntax and helper tests after any regression fix**

Run:

```bash
node --test tests/test_technical_analysis_helpers.mjs
node --check src/web/technical-analysis.js
```

Expected:

```text
PASS with no syntax errors.
```

- [ ] **Step 4: Create the final integration commit if Task 4 changed code**

Run:

```bash
git add src/web/index.css src/web/technical-analysis.js src/web/technical-analysis.html
git commit -m "fix: tighten technical analysis page integration"
```
