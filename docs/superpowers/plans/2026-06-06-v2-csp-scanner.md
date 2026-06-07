# V2 CSP Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. UI-building tasks (CSS, filter sheet, cards) MUST invoke the **frontend-design** skill to match the v2 visual language.

**Goal:** Replace the v2 Scanner handoff card with a full native port of the v1 CSP Universe Scanner, organized as a results-first view with a slide-up filter sheet, built into the v2 SPA.

**Architecture:** A new self-contained module `src/web/v2/scanner.js` exposes `ScannerView.render(rootEl)` and `ScannerView.teardown()`. The v2 router in `app.js` delegates the `scanner` case to it. All scanner logic (state, query building, scan fetch + retry, funnel, candidate cards, stock performance, filter sheet) lives in the module. Reuses existing `/api/screener/csp-scan*`, `/api/market-data/*`, and `/api/screener/stocks` endpoints — no API or Dockerfile changes.

**Tech Stack:** Vanilla JS, CSS custom properties (v2 tokens: IBM Plex Mono/Sans Condensed), existing `/api/*` endpoints. No build step. Verification via Playwright MCP against dev (`https://dev-mi.austin10berge.com`).

**Spec:** `docs/superpowers/specs/2026-06-06-v2-csp-scanner-design.md`

---

## Reference Contracts (read before starting)

**v1 source to port from:** `src/web/scanner.js` (970 lines). Key functions and line ranges:
- `_state` + defaults: lines 38–70
- `_persistParams` / `_restoreParams` (localStorage key `market-intelligence:csp-scanner-params`): 74–105
- `PARAM_CONFIG` (drives param labels/ranges/scaling): 122–138
- `_buildQueryString` (maps internal keys → API query params): search `function _buildQueryString`
- `startScan` / `forceRescan` / `_refreshMarketData` / `_fetchScan` / `_handleScanResult`: 477–636
- Conditions picker: `loadAvailableConditions` (154), `renderConditionPicker`
- Sectors picker: `_loadSectors` (376), `_renderSectorFilter`, `_renderSectorChips`
- Stock Performance: search `screener/stocks` (line ~642) and `renderStockTable`/`sortStocks`

**Internal-key → API-query-param mapping** (from `_buildQueryString`):
`min_cap→min_cap, max_price→max_price, min_beta→min_beta, max_beta→max_beta, min_vol→min_vol, rsi_max→max_rsi, adx_min→min_adx, adx_max→max_adx, dte_min→min_dte, dte_max→max_dte, conditions→conditions(csv), min_fcf_b, max_debt_to_equity, min_revenue_growth, min_earnings_growth, min_dividend_yield, restrict_to_watchlist_universe→'true' if set, sectors→csv`. Growth/yield values are stored as fractions (e.g. `-0.10`) and shown ×100.

**Scan response shape** (`GET /api/screener/csp-scan`):
```
{ filter_summary: { combined_unique, fundamental_passed, vol_passed, technical_passed, options_screener_returned },
  candidates: [ { symbol, strike, dte, delta, roc_percent, annualized_roc, current_price, impliedVolatility, premium, otm_percent, ... } ],
  cached: bool, cached_at: iso|null, market_status: str }
```

**v2 option-card markup to reuse** (`src/web/v2/app.js:393–408`) — fields: `symbol, strike, dte, delta, roc_percent, annualized_roc, current_price, impliedVolatility, premium, otm_percent`. Tier classes: `roc>=3 → 'up'`, `roc>=1.5 → 'tier-mid'`. Pagination pattern: `src/web/v2/app.js:410–422` (`CSP_PER_PAGE = 7`).

> **Porting note:** For heavy v1 logic (param persistence, query building, conditions/sectors pickers, stock table), copy the named v1 function into `scanner.js`, then apply the adaptations each task lists (rename DOM ids, scope into the module, swap card markup). Do NOT re-derive from scratch.

---

## Task 1: Module scaffold + router wiring

**Files:**
- Create: `src/web/v2/scanner.js`
- Modify: `src/web/v2/index.html` (add `<script>` before `app.js`)
- Modify: `src/web/v2/app.js` (router `case 'scanner'`, teardown on tab switch)

- [ ] **Step 1: Create the module skeleton**

```javascript
// src/web/v2/scanner.js — v2 CSP Universe Scanner (native port of /scanner.js)
// Exposes window.ScannerView.{render, teardown}. Reuses /api/screener/csp-scan*.
(function () {
    'use strict';

    const API_BASE = (window.API_CONFIG && window.API_CONFIG.baseUrl) || '/api';
    const SCANNER_PARAMS_KEY = 'market-intelligence:csp-scanner-params';

    const _state = {
        params: null,            // filled by _restoreParams() over defaults (Task 2)
        availableConditions: [],
        availableSectors: [],
        scanPollId: null,
        scanStart: null,
        candidates: [],
        cspPage: 1,
        lastResult: null,
    };
    const CSP_PER_PAGE = 7;

    function render(rootEl) {
        rootEl.innerHTML = '<div class="scanner-view"><div class="list-message loading">Loading scanner…</div></div>';
        // Task 3+ fill this in.
    }

    function teardown() {
        if (_state.scanPollId) { clearInterval(_state.scanPollId); _state.scanPollId = null; }
    }

    window.ScannerView = { render, teardown };
})();
```

- [ ] **Step 2: Add the script tag to index.html**

In `src/web/v2/index.html`, change the script block (currently lines ~586–587) to load the module before `app.js`:

```html
    <script src="/config.js"></script>
    <script src="./scanner.js"></script>
    <script src="./app.js"></script>
```

- [ ] **Step 3: Wire the router in app.js**

In `src/web/v2/app.js`, replace the `scanner` case in `switchTab()` and add teardown of the previous view. Current `switchTab` (verbatim) is:

```javascript
function switchTab(tab) {
    activeTab = tab;
    renderBottomNav();
    switch (tab) {
        case 'overview':   renderOverviewView();  break;
        case 'watchlist':  renderWatchlistView(); break;
        case 'scanner':    renderHandoffView('Scanner',    '/scanner.html',  'Universe and watchlist CSP scanning with custom parameters.'); break;
        case 'backtester': renderHandoffView('Backtester', '/backtest.html', 'Backtest CSP strategies against historical price data.');        break;
    }
}
```

Replace with:

```javascript
function switchTab(tab) {
    // Tear down the scanner's poll timer when navigating away from it.
    if (activeTab === 'scanner' && tab !== 'scanner' && window.ScannerView) {
        window.ScannerView.teardown();
    }
    activeTab = tab;
    renderBottomNav();
    const mainContent = document.getElementById('main-content');
    switch (tab) {
        case 'overview':   renderOverviewView();  break;
        case 'watchlist':  renderWatchlistView(); break;
        case 'scanner':    window.ScannerView.render(mainContent); break;
        case 'backtester': renderHandoffView('Backtester', '/backtest.html', 'Backtest CSP strategies against historical price data.'); break;
    }
}
```

- [ ] **Step 4: Verify in browser (Playwright MCP, local dashboard)**

Deploy the v2 dir to the running dashboard (the `src/web/v2/` mount is served at `/v2/`). Then:
- `browser_navigate` → `https://dev-mi.austin10berge.com/v2/` (after deploy) **or** local equivalent.
- `browser_click` the Scanner nav item.
- `browser_snapshot` → expect the "Loading scanner…" placeholder inside `.scanner-view`, and the bottom nav `scanner` item marked active.
- Click Overview, then Scanner again → no console errors (`browser_console_messages`).

Expected: Scanner tab renders the module placeholder (not the old handoff card); switching away clears timers.

- [ ] **Step 5: Commit** (only if the user has authorized commits — otherwise leave staged for review)

```bash
git add src/web/v2/scanner.js src/web/v2/index.html src/web/v2/app.js
git commit -m "feat(v2): scaffold native CSP scanner module + router wiring"
```

---

## Task 2: Param state, defaults, persistence, query builder

**Files:**
- Modify: `src/web/v2/scanner.js`

- [ ] **Step 1: Add defaults + restore/persist (port from scanner.js:38–105)**

Set `_state.params` defaults exactly as v1 (`src/web/scanner.js:41–60`):

```javascript
const DEFAULT_PARAMS = {
    min_cap: 10, max_price: 150, min_beta: 0.8, max_beta: 2.4, min_vol: 30,
    rsi_max: 50, adx_min: 15, adx_max: 50, dte_min: 3, dte_max: 46,
    conditions: [],
    min_fcf_b: 0, max_debt_to_equity: 2.0, min_revenue_growth: -0.10,
    min_earnings_growth: null, min_dividend_yield: null,
    restrict_to_watchlist_universe: false, sectors: [],
};
```

Port `_restoreParams()` and `_persistParams()` verbatim from `scanner.js:74–105`, but initialize from a fresh copy of `DEFAULT_PARAMS`:

```javascript
function _restoreParams() {
    _state.params = { ...DEFAULT_PARAMS, conditions: [], sectors: [] };
    try {
        const raw = window.localStorage.getItem(SCANNER_PARAMS_KEY);
        if (!raw) return;
        const saved = JSON.parse(raw);
        const p = _state.params;
        // ... copy the exact type-guarded assignments from scanner.js:86–103 ...
    } catch { /* corrupt storage — use defaults */ }
}
function _persistParams() {
    try { window.localStorage.setItem(SCANNER_PARAMS_KEY, JSON.stringify(_state.params)); } catch {}
}
```

Call `_restoreParams()` at the top of `render()` (replacing nothing else yet).

- [ ] **Step 2: Add `_buildQueryString()` (port verbatim from scanner.js)**

Copy `_buildQueryString()` exactly (the internal→API key mapping documented above). It reads `_state.params`.

- [ ] **Step 3: Add `PARAM_CONFIG`** — copy `scanner.js:122–138` verbatim into the module (used by the active-params summary and the filter sheet).

- [ ] **Step 4: Verify (browser console)**

Reload `/v2/`, open Scanner. In `browser_evaluate`, run a sanity probe that the module restored params and builds a query string. Since the module is IIFE-scoped, add a temporary debug hook at the end of the IIFE: `window.ScannerView._debug = { state: _state, buildQueryString: _buildQueryString };` (remove in Task 9). Then:
- `browser_evaluate` → `() => window.ScannerView._debug.buildQueryString()`
- Expected: a string like `min_cap=10&max_price=150&min_beta=0.8&max_beta=2.4&min_vol=30&max_rsi=50&min_adx=15&max_adx=50&min_dte=3&max_dte=46&min_fcf_b=0&max_debt_to_equity=2&min_revenue_growth=-0.1`.

- [ ] **Step 5: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js
git commit -m "feat(v2): scanner param state, persistence, query builder"
```

---

## Task 3: Load conditions, sectors, and data freshness

**Files:**
- Modify: `src/web/v2/scanner.js`

- [ ] **Step 1: Port fetchers**

Port from `scanner.js`: `loadAvailableConditions()` (154–163), `_loadSectors()` (search `screener/csp-scan/sectors`), and a `loadDataFreshness()` that fetches `GET /api/market-data/status` and stashes `{ stale_hours, is_stale }` in `_state.freshness`. Strip any direct DOM writes from the v1 versions — these only populate `_state`.

```javascript
async function loadDataFreshness() {
    try {
        const res = await fetch(`${API_BASE}/market-data/status`);
        if (!res.ok) { _state.freshness = null; return; }
        _state.freshness = await res.json();   // { is_stale, stale_hours, ... }
    } catch { _state.freshness = null; }
}
```

- [ ] **Step 2: Parallel-load in render()**

In `render()`, after `_restoreParams()`, kick off the three loads in parallel and re-render dependent UI when ready:

```javascript
Promise.all([loadAvailableConditions(), _loadSectors(), loadDataFreshness()])
    .then(() => { _renderHeader(); _renderActiveParams(); });  // helpers from Task 4
```

- [ ] **Step 3: Verify**

`browser_evaluate` → `() => window.ScannerView._debug.state.availableConditions.length` → expect > 0; same for `availableSectors`. `_state.freshness` is an object or null.

- [ ] **Step 4: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js
git commit -m "feat(v2): scanner loads conditions, sectors, freshness"
```

---

## Task 4: Main view shell (header, active-params summary, results containers)

**Files:**
- Modify: `src/web/v2/scanner.js`
- Modify: `src/web/v2/index.html` (CSS)

> **Invoke the frontend-design skill for this task's CSS** so the header, freshness badge, funnel, and chip row match v2 tokens.

- [ ] **Step 1: Build the shell markup in render()**

Replace the placeholder in `render()` with the three zones + containers the later tasks populate:

```javascript
function render(rootEl) {
    _restoreParams();
    rootEl.innerHTML = `
      <div class="scanner-view">
        <div class="scanner-header">
          <div class="scanner-title">CSP Scanner</div>
          <span class="data-freshness-badge" id="scn-freshness">…</span>
          <button class="scn-filters-btn" id="scn-filters-btn">Filters</button>
        </div>
        <div class="scn-active-params" id="scn-active-params"></div>
        <div class="scn-funnel" id="scn-funnel" style="display:none"></div>
        <div class="scn-stockperf" id="scn-stockperf" style="display:none"></div>
        <div id="scn-results"><div class="list-message loading">Loading scanner…</div></div>
        <div class="option-pagination" id="scn-pagination" style="display:none"></div>
        <div class="scn-sheet-root" id="scn-sheet-root"></div>
      </div>`;
    document.getElementById('scn-filters-btn').addEventListener('click', openFilterSheet); // Task 8
    Promise.all([loadAvailableConditions(), _loadSectors(), loadDataFreshness()])
        .then(() => { _renderHeader(); _renderActiveParams(); runScan(); });  // runScan from Task 5
}
```

- [ ] **Step 2: `_renderHeader()` — freshness badge + filter count**

```javascript
function _renderHeader() {
    const badge = document.getElementById('scn-freshness');
    const f = _state.freshness;
    if (!f) { badge.textContent = 'data: unknown'; badge.className = 'data-freshness-badge empty'; }
    else if (f.is_stale) { badge.textContent = `data ${Math.round(f.stale_hours)}h old`; badge.className = 'data-freshness-badge stale'; }
    else { badge.textContent = 'data fresh'; badge.className = 'data-freshness-badge fresh'; }
    const n = _activeFilterCount();
    document.getElementById('scn-filters-btn').textContent = n ? `Filters · ${n}` : 'Filters';
}
```

- [ ] **Step 3: `_activeFilterCount()` + `_renderActiveParams()`**

`_activeFilterCount()` returns how many params differ from `DEFAULT_PARAMS` (compare scalars; count non-empty `conditions`/`sectors`; count `restrict_to_watchlist_universe` if true). `_renderActiveParams()` renders a chip row using `PARAM_CONFIG` labels for non-default scalar params plus one chip per selected sector/condition and a "S&P+NDX only" chip when restricted. Clicking the row opens the sheet:

```javascript
function _renderActiveParams() {
    const el = document.getElementById('scn-active-params');
    const chips = [];
    for (const cfg of PARAM_CONFIG) {
        const v = _state.params[cfg.key];
        if (v == null || v === DEFAULT_PARAMS[cfg.key]) continue;
        const shown = cfg.scale ? (v * cfg.scale) : v;
        chips.push(`<span class="scn-chip">${cfg.label} ${shown}${cfg.suffix||''}</span>`);
    }
    _state.params.conditions.forEach(c => chips.push(`<span class="scn-chip">${escapeHtml(c)}</span>`));
    _state.params.sectors.forEach(s => chips.push(`<span class="scn-chip">${escapeHtml(s)}</span>`));
    if (_state.params.restrict_to_watchlist_universe) chips.push('<span class="scn-chip">S&P+NDX only</span>');
    el.innerHTML = chips.length ? chips.join('') : '<span class="scn-chip muted">Default filters</span>';
    el.onclick = openFilterSheet;
}
```

(Add a local `escapeHtml` helper if `app.js`'s `escHtml` is not in module scope — it is not, since `scanner.js` is a separate IIFE.)

- [ ] **Step 4: Add CSS (frontend-design)** — in `index.html` `<style>`, add rules for `.scanner-view`, `.scanner-header`, `.scanner-title`, `.data-freshness-badge` (+ `.fresh`/`.stale`/`.empty` states), `.scn-filters-btn`, `.scn-active-params`, `.scn-chip` (+ `.muted`). Reuse existing color tokens (`--tv-surface`, `--tv-border`, `--tv-blue`, `--tv-muted`). Funnel/sheet styles come in later tasks.

- [ ] **Step 5: Verify (browser)**

Open Scanner: expect header with title + freshness badge + Filters button, and an active-params chip row. `browser_take_screenshot` for visual confirmation against v2 style.

- [ ] **Step 6: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js src/web/v2/index.html
git commit -m "feat(v2): scanner shell — header, freshness badge, active-params chips"
```

---

## Task 5: Scan fetch + retry loop + funnel strip

**Files:**
- Modify: `src/web/v2/scanner.js`
- Modify: `src/web/v2/index.html` (funnel CSS)

> **Invoke frontend-design for the funnel CSS.**

- [ ] **Step 1: Port the fetch + retry loop**

Port `_fetchScan()` (scanner.js:571–587) and the retry pattern from `startScan()` (488–496). Note: the request is **synchronous on the server (3–6 min cold)**; `_fetchScan` returns `true` on any HTTP 200 and `false` on error. The interval is a **retry-on-failure**, not a progress poll. Adapt to module scope, writing status into `#scn-results`:

```javascript
async function runScan() {
    teardown();                       // clear any prior timer
    _state.scanStart = Date.now();
    _setResultsMessage('<span class="spinner"></span> Scanning… cold scans take 3–6 min.');
    const done = await _fetchScan();
    if (!done) {
        _state.scanPollId = setInterval(async () => {
            if (await _fetchScan()) { teardown(); }
        }, 6000);
    }
}
async function _fetchScan() {
    try {
        const res = await fetch(`${API_BASE}/screener/csp-scan?${_buildQueryString()}`);
        if (!res.ok) { _setResultsMessage(`Server error: ${res.status}`); return false; }
        _handleScanResult(await res.json());
        return true;
    } catch (e) { _setResultsMessage(`Network error: ${e.message}`); return false; }
}
```

- [ ] **Step 2: `_handleScanResult()` — funnel + stash candidates (port from scanner.js:591–636)**

```javascript
function _handleScanResult(data) {
    _state.lastResult = data;
    _state.candidates = data.candidates || [];
    _renderFunnel(data);
    _state.cspPage = 1;
    _renderCandidates();              // Task 6
    _renderStockPerf(_state.candidates); // Task 7
}
```

- [ ] **Step 3: `_renderFunnel()`**

```javascript
function _renderFunnel(data) {
    const s = data.filter_summary || {};
    const cache = data.cached
        ? `cached · ${data.cached_at ? _timeAgo(data.cached_at) : ''}`
        : `live · ${data.market_status || ''}`;
    const cell = (label, val) => `<div class="fc"><div class="fc-label">${label}</div><div class="fc-val">${val ?? '—'}</div></div>`;
    const el = document.getElementById('scn-funnel');
    el.style.display = '';
    el.innerHTML =
        cell('Universe', s.combined_unique) + cell('Fundamental', s.fundamental_passed) +
        cell('Vol', s.vol_passed) + cell('Technical', s.technical_passed) +
        cell('Candidates', s.options_screener_returned) +
        `<div class="fc fc-cache">${cache}</div>`;
}
```

Add helpers `_setResultsMessage(html)`, `_timeAgo(iso)` (port from scanner.js `_timeAgo`).

- [ ] **Step 4: Add funnel CSS (frontend-design)** — `.scn-funnel` (horizontal flex, scrollable on narrow), `.fc`, `.fc-label`, `.fc-val`, `.fc-cache`.

- [ ] **Step 5: Verify (browser)** — Open Scanner; if a cached EOD result exists it appears in seconds. Expect 5 funnel cells with numbers + a cache/live pill. `browser_snapshot` to assert cell values are numeric.

- [ ] **Step 6: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js src/web/v2/index.html
git commit -m "feat(v2): scanner scan fetch + retry + funnel strip"
```

---

## Task 6: Candidate list (reuse v2 option-card) + sort + pagination

**Files:**
- Modify: `src/web/v2/scanner.js`

- [ ] **Step 1: Render candidates with the v2 option-card markup**

Reuse the exact card structure from `app.js:393–408` (fields verified above). First confirm the scanner candidate shape matches via `browser_evaluate` on `_state.lastResult.candidates[0]`; if a field name differs (e.g. scanner may name it `iv` vs `impliedVolatility`), adjust the accessor and note it.

```javascript
function _renderCandidates() {
    const sorted = [..._state.candidates].sort((a, b) =>
        (parseFloat(b.annualized_roc) || 0) - (parseFloat(a.annualized_roc) || 0));
    const totalPages = Math.max(1, Math.ceil(sorted.length / CSP_PER_PAGE));
    _state.cspPage = Math.min(_state.cspPage, totalPages);
    const slice = sorted.slice((_state.cspPage - 1) * CSP_PER_PAGE, _state.cspPage * CSP_PER_PAGE);
    const listEl = document.getElementById('scn-results');
    if (!slice.length) { listEl.innerHTML = '<div class="list-message">No CSP candidates found</div>'; _renderPager(0); return; }
    listEl.innerHTML = slice.map((c, i) => {
        const roc = parseFloat(c.roc_percent) || 0;
        const yld = c.annualized_roc ? `${parseFloat(c.annualized_roc).toFixed(1)}%y` : '—';
        const tierCls = roc >= 3 ? 'up' : roc >= 1.5 ? 'tier-mid' : '';
        return `<div class="option-card ${tierCls}" style="--row-delay:${i*20}ms" onclick="openTradingView('${escapeHtml(c.symbol)}')">
            <div class="oc-row1">
                <span class="oc-symbol">${escapeHtml(c.symbol)}</span>
                <span class="oc-meta"><span style="color:#fff">$${c.strike.toFixed(2)}</span> · ${c.dte ?? '—'}d · Δ${c.delta != null ? c.delta.toFixed(2) : '—'}</span>
                <span class="oc-highlight">${roc.toFixed(2)}% ROC</span>
            </div>
            <div class="oc-row2">
                <span class="oc-name">$${c.current_price.toFixed(2)} · IV ${c.impliedVolatility != null ? c.impliedVolatility.toFixed(1) + '%' : '—'}</span>
                <span class="oc-metrics"><span class="oc-prem">$${c.premium.toFixed(2)}</span> · <span class="oc-yield">${yld}</span> · ${c.otm_percent}% OTM</span>
            </div>
        </div>`;
    }).join('');
    _renderPager(totalPages);
}
```

Note: `openTradingView` is a global defined in `app.js` — it is in scope on `window`, so `onclick` works.

- [ ] **Step 2: Pagination (reuse app.js:410–422 pattern)**

```javascript
function _renderPager(totalPages) {
    const el = document.getElementById('scn-pagination');
    if (totalPages <= 1) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    el.innerHTML = `
        <button class="page-btn" ${_state.cspPage<=1?'disabled':''} onclick="ScannerView._step(-1)">←</button>
        <span class="page-info-text">${_state.cspPage} / ${totalPages}</span>
        <button class="page-btn" ${_state.cspPage>=totalPages?'disabled':''} onclick="ScannerView._step(1)">→</button>`;
}
```

Expose `_step` on the public object: `window.ScannerView = { render, teardown, _step: (d) => { _state.cspPage += d; _renderCandidates(); } };`

- [ ] **Step 3: Verify (browser)** — Open Scanner with a cached result; expect v2 option-cards rendered, sorted by annualized yield desc, pager visible when >7 candidates. Click a card → opens TradingView (`browser_console_messages` shows no error). Click pager → page advances.

- [ ] **Step 4: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js
git commit -m "feat(v2): scanner candidate cards + pagination"
```

---

## Task 7: Stock Performance collapsible block

**Files:**
- Modify: `src/web/v2/scanner.js`
- Modify: `src/web/v2/index.html` (CSS)

> **Invoke frontend-design for the collapsible block + perf-row CSS.**

- [ ] **Step 1: Fetch + render**

Port v1's stock fetch (`scanner.js` ~642, `GET /api/screener/stocks?tickers=…`) and table render, restyled as a collapsible block, collapsed by default. Failure hides the block quietly (non-critical):

```javascript
async function _renderStockPerf(candidates) {
    const tickers = [...new Set(candidates.map(c => c.symbol))];
    const host = document.getElementById('scn-stockperf');
    if (!tickers.length) { host.style.display = 'none'; return; }
    try {
        const res = await fetch(`${API_BASE}/screener/stocks?tickers=${tickers.join(',')}`);
        if (!res.ok) { host.style.display = 'none'; return; }
        const data = await res.json();
        const rows = (data.stocks || data || []);   // confirm shape against v1 handler
        host.style.display = '';
        host.innerHTML = `
          <button class="scn-perf-toggle" onclick="ScannerView._togglePerf()">Stock Performance (${rows.length}) ▾</button>
          <div class="scn-perf-body" id="scn-perf-body" style="display:none">
            ${rows.map(_perfRow).join('')}
          </div>`;
    } catch { host.style.display = 'none'; }
}
function _perfRow(s) {
    const pct = (v) => v == null ? '—' : `<span class="${v>=0?'text-green':'text-red'}">${v>=0?'+':''}${Number(v).toFixed(1)}%</span>`;
    return `<div class="scn-perf-row"><span class="scn-perf-sym">${escapeHtml(s.symbol)}</span>
      <span>1D ${pct(s.pct_1d)}</span><span>1W ${pct(s.pct_1w)}</span></div>`;  // match v1 fields
}
```

Expose `_togglePerf` on the public object (toggles `#scn-perf-body` display + caret).

- [ ] **Step 2: Confirm field names** — `browser_evaluate` on the `/screener/stocks` response (or read the v1 `renderStockTable`) to confirm `symbol`, `pct_1d`, `pct_1w` (and any others to show). Adjust `_perfRow` accordingly.

- [ ] **Step 3: Add CSS (frontend-design)** — `.scn-perf-toggle`, `.scn-perf-body`, `.scn-perf-row`, `.scn-perf-sym`, reuse `.text-green`/`.text-red`.

- [ ] **Step 4: Verify (browser)** — Block appears collapsed below funnel; clicking expands rows with colored 1D/1W moves.

- [ ] **Step 5: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js src/web/v2/index.html
git commit -m "feat(v2): scanner stock performance block"
```

---

## Task 8: Filter sheet overlay + Apply & Scan + Force Rescan

**Files:**
- Modify: `src/web/v2/scanner.js`
- Modify: `src/web/v2/index.html` (sheet CSS)

> **Invoke frontend-design for the full-screen sheet, sections, inputs, chips, and sticky footer.**

- [ ] **Step 1: Build the sheet markup**

`openFilterSheet()` populates `#scn-sheet-root` with a full-screen overlay containing sections, then animates in. Inputs are seeded from `_state.params`. Sections:
- **Universe**: number inputs for `min_cap, max_price, min_beta, max_beta, min_vol, rsi_max, adx_min, adx_max, dte_min, dte_max` (use `PARAM_CONFIG` for labels/min/max/step).
- **Fundamentals**: `min_fcf_b, max_debt_to_equity, min_revenue_growth, min_earnings_growth, min_dividend_yield` (growth/yield shown ×100, written back ÷100).
- **Technical Conditions**: chip toggles from `_state.availableConditions` (selected reflect `_state.params.conditions`).
- **Sectors**: checkboxes from `_state.availableSectors` (checked reflect `_state.params.sectors`).
- **Universe toggle**: `restrict_to_watchlist_universe`.
- Sticky footer: **Apply & Scan**, **Force Rescan**, and a **Close** (×) affordance.

```javascript
function openFilterSheet() {
    const root = document.getElementById('scn-sheet-root');
    root.innerHTML = _sheetHtml();        // builds sections from PARAM_CONFIG + _state
    root.classList.add('open');
    _wireSheetEvents(root);               // condition/sector toggles update a working copy
}
function closeFilterSheet() {
    const root = document.getElementById('scn-sheet-root');
    root.classList.remove('open');
    root.innerHTML = '';
}
```

- [ ] **Step 2: Apply & Scan**

Read all sheet inputs into `_state.params` (dividing scaled fields by their `cfg.scale`), persist, close sheet, re-render header + active-params, then `runScan()`:

```javascript
function applyAndScan() {
    _readSheetIntoParams();   // scalar inputs, conditions, sectors, toggle
    _persistParams();
    closeFilterSheet();
    _renderHeader(); _renderActiveParams();
    runScan();
}
```

- [ ] **Step 3: Force Rescan (port scanner.js:499–569)**

Port `forceRescan()` + `_refreshMarketData()`: read sheet into params first, then check `/market-data/status`; if `stale_hours > 18`, `POST /market-data/refresh` and wait (20s poll, 6-min cap, reusing the v1 loop); then `DELETE /screener/csp-scan?<query>`; then `runScan()`. Status messages write into `#scn-results`.

- [ ] **Step 4: Wire footer buttons** — expose `applyAndScan`, `forceRescan`, `closeFilterSheet` via `window.ScannerView` (or attach listeners in `_wireSheetEvents`).

- [ ] **Step 5: Add CSS (frontend-design)** — `.scn-sheet-root` (fixed, full-screen, translateY off-screen; `.open` slides in), `.scn-sheet-section`, `.scn-sheet-input`, condition/sector chip + checkbox styles, sticky `.scn-sheet-footer` with primary/secondary buttons. Respect the fixed bottom nav (sheet z-index above nav, footer clears it).

- [ ] **Step 6: Verify (browser)** — Tap Filters → sheet slides up with all sections seeded from current params. Change `min_vol` to 40, toggle a sector, tap **Apply & Scan** → sheet closes, active-params chip row updates, scan re-runs with `min_vol=40` (confirm via `browser_network_requests` that the request query includes `min_vol=40&sectors=…`). Tap Filters → **Force Rescan** → confirm a `DELETE` then `GET` fire (`browser_network_requests`).

- [ ] **Step 7: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js src/web/v2/index.html
git commit -m "feat(v2): scanner filter sheet, apply & scan, force rescan"
```

---

## Task 9: Auto-load polish + remove debug hook + teardown audit

**Files:**
- Modify: `src/web/v2/scanner.js`

- [ ] **Step 1: Confirm auto-load** — `render()` already calls `runScan()` after the parallel loads (Task 4). Verify first-visit behavior: a cached EOD result shows instantly; no cache triggers a live scan with a clear "cold scan 3–6 min" message.

- [ ] **Step 2: Remove the temporary `_debug` hook** added in Task 2.

- [ ] **Step 3: Teardown audit** — confirm `switchTab` away from scanner calls `ScannerView.teardown()` (Task 1) and that `teardown()` clears `scanPollId`. Add a guard so a scan result arriving after teardown (navigated away) does not throw: in `_handleScanResult`, bail if `document.getElementById('scn-funnel')` is null.

- [ ] **Step 4: Verify (browser)** — Start a scan, immediately switch to Overview, switch back → no console errors, no duplicate timers (`browser_console_messages` clean).

- [ ] **Step 5: Commit** (if authorized)

```bash
git add src/web/v2/scanner.js
git commit -m "feat(v2): scanner auto-load polish + teardown safety"
```

---

## Task 10: End-to-end verification against dev

**Files:** none (verification only)

- [ ] **Step 1: Deploy v2 to dev** — ensure `src/web/v2/` is served by the dev dashboard (it is COPYed wholesale; for a worktree, the dashboard serves the main folder — copy or rebuild per CLAUDE.md "Worktree → Dev Dashboard Testing").

- [ ] **Step 2: Full walkthrough (Playwright MCP against `https://dev-mi.austin10berge.com/v2/`)**
  - Scanner tab loads → funnel + cards + freshness badge render from cache.
  - Open Filters → change a param → Apply & Scan → results + active-params update; network request carries the new param.
  - Expand Stock Performance → rows render.
  - Paginate candidate cards.
  - Navigate away and back → no stray timers / console errors.
  - `browser_take_screenshot` (desktop + mobile viewport via `browser_resize`) for visual confirmation against v2 style.

- [ ] **Step 3: Cross-check vs v1** — open `/scanner.html`, run the same params, confirm the v2 candidate set matches (shared cache key → identical results).

- [ ] **Step 4: Update memory** — append a note to `project_v2_spa_refactor.md` that the Scanner view is now native (and Backtester remains a handoff card).

---

## Self-Review (completed by plan author)

- **Spec coverage:** Full faithful port (T2–T8) ✓; filter sheet (T8) ✓; option-card reuse + Stock Perf (T6, T7) ✓; auto-load cached snapshot (T4/T9) ✓; shared localStorage key (T2) ✓; separate `scanner.js` module (T1) ✓; funnel/freshness/force-rescan (T5/T4/T8) ✓.
- **Placeholders:** New/changed code shown in full; heavy v1 logic referenced by exact function + line range with explicit adaptations (porting note), not vague TODOs.
- **Type consistency:** `_state`, `runScan`, `_fetchScan`, `_handleScanResult`, `_renderFunnel`, `_renderCandidates`, `_renderStockPerf`, `openFilterSheet`/`applyAndScan`/`forceRescan`, public `ScannerView.{render, teardown, _step, _togglePerf}` consistent across tasks. Candidate field names match `app.js:393–408`; one verification step (T6/T7) guards against scanner-vs-watchlist field drift.
