# V2 SPA Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bottom tab bar + full SPA routing to the v2 dashboard, with Market Overview and all three Watchlist sub-tabs (Tickers, CSP, LEAPS) built natively; Scanner and Backtester show a handoff card linking to the v1 pages.

**Architecture:** Single-page app inside `src/web/v2/index.html` + `src/web/v2/app.js`. A fixed bottom nav bar switches between views by re-rendering a `#main-content` div. Data is lazy-loaded per view on first visit and cached in module-level arrays. The status bar (posture pill) stays fixed across all views.

**Tech Stack:** Vanilla JS, CSS custom properties, IBM Plex Mono / IBM Plex Sans Condensed fonts, existing `/api/*` endpoints.

---

## File Map

| File | Changes |
|------|---------|
| `src/web/v2/index.html` | Strip hardcoded watchlist divs → single `#main-content` div; add `.bottom-nav`; add CSS for nav, overview panels, option cards, sub-tabs, handoff view |
| `src/web/v2/app.js` | Add routing state + `switchTab()`; add `renderBottomNav()`; add `renderWatchlistView()` + sub-tab logic; add `renderTickersContent()` (refactor of current init); add `renderCspContent()` + CSP fetch/render/sort/paginate; add `renderLeapsContent()` + LEAPS fetch/render/sort/paginate; add `renderOverviewView()` + port all Market Overview fetch/render functions; add `renderHandoffView()`; update `DOMContentLoaded` |
| `Dockerfile` | No change — `COPY src/web/v2/ /usr/share/nginx/html/v2/` already present |

---

## Task 1: Restructure HTML shell — add `#main-content` + bottom nav

**Files:**
- Modify: `src/web/v2/index.html`

- [ ] **Step 1: Replace body content** — strip the hardcoded section-header, col-tabs, col-header-row, and stocks-list divs. Replace with a single `#main-content` div and a `.bottom-nav` nav. The status bar stays. Final body:

```html
<body>
    <div class="status-bar">
        <span class="status-title">Market Intelligence</span>
        <div class="status-right">
            <span class="composite-score" id="composite-score"></span>
            <div class="posture-pill loading" id="posture-widget">
                <span class="pulse-dot"></span>Loading
            </div>
        </div>
    </div>

    <div id="main-content"></div>

    <nav class="bottom-nav" id="bottom-nav"></nav>

    <script src="/config.js"></script>
    <script src="./app.js"></script>
</body>
```

- [ ] **Step 2: Add bottom nav CSS** — add these rules inside the `<style>` block (remove old `.sort-bar`, `.sort-btn` rules; they move to JS-rendered elements but the `.col-tab` rules stay):

```css
/* ── Bottom navigation ── */
.bottom-nav {
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: 56px;
    background: var(--tv-surface);
    border-top: 1px solid var(--tv-border);
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    z-index: 100;
    -webkit-tap-highlight-color: transparent;
}

.nav-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    color: var(--tv-muted);
    font-size: 9px;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    cursor: pointer;
    border: none;
    background: none;
    transition: color 0.15s;
    padding: 0;
}
.nav-item.active { color: var(--tv-blue); }
.nav-item svg { width: 20px; height: 20px; }

/* ── Main content area clears the fixed nav ── */
#main-content { padding-bottom: 64px; }

/* ── Watchlist sub-tabs ── */
.watchlist-sub-tabs {
    display: flex;
    gap: 6px;
}
.sub-tab {
    padding: 4px 12px;
    border-radius: 6px;
    border: 1px solid var(--tv-border);
    background: var(--tv-surface);
    color: var(--tv-muted);
    font-size: 10px;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
    letter-spacing: 0.03em;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s, background 0.15s;
    white-space: nowrap;
}
.sub-tab.active {
    color: var(--tv-blue);
    border-color: var(--tv-blue);
    background: rgba(41,98,255,0.12);
}

/* ── Option cards (CSP + LEAPS) ── */
.option-card {
    padding: 10px 14px;
    border-bottom: 1px solid var(--tv-border);
    border-left: 2px solid var(--tv-border);
    cursor: pointer;
    transition: background 0.1s;
    animation: row-in 0.32s ease both;
    animation-delay: var(--row-delay, 0ms);
}
.option-card.up   { border-left-color: var(--tv-green); }
.option-card.down { border-left-color: var(--tv-red); }
@media (hover: hover) { .option-card:hover { background: var(--tv-surface); } }
.option-card:active { background: var(--tv-surface2); }

.oc-row1 {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 3px;
}
.oc-symbol {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    color: #fff;
    letter-spacing: 0.03em;
    flex-shrink: 0;
}
.oc-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--tv-muted);
    flex-shrink: 0;
}
.oc-highlight {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    font-weight: 600;
    color: var(--tv-green);
    margin-left: auto;
}
.oc-row2 {
    display: flex;
    align-items: center;
    gap: 8px;
}
.oc-name {
    font-size: 11px;
    color: var(--tv-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    min-width: 0;
    flex: 1;
}
.oc-metrics {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--tv-muted);
    flex-shrink: 0;
    text-align: right;
}

/* Pagination */
.option-pagination {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--tv-border);
}
.page-btn {
    width: 28px; height: 28px;
    border-radius: 50%;
    border: 1px solid var(--tv-border);
    background: var(--tv-surface);
    color: var(--tv-text);
    font-size: 14px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.15s;
}
.page-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.page-btn:not(:disabled):hover { background: var(--tv-surface2); }
.page-info-text {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 10px;
    color: var(--tv-muted);
}

/* ── Market Overview ── */
.overview-section { padding: 0; }

.overview-card {
    margin: 8px 14px;
    padding: 12px 14px;
    background: var(--tv-surface);
    border: 1px solid var(--tv-border);
    border-radius: 8px;
}
.overview-card-title {
    font-size: 9px;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--tv-muted);
    margin-bottom: 8px;
}
.overview-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0 8px;
    padding: 0 14px 8px;
}
.overview-grid .overview-card {
    margin: 0 0 8px 0;
}

.llm-text {
    font-size: 12px;
    color: var(--tv-text);
    line-height: 1.6;
    white-space: pre-wrap;
}

.posture-detail-score {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 22px;
    font-weight: 600;
    margin-bottom: 4px;
}
.posture-detail-label {
    font-size: 11px;
    color: var(--tv-muted);
}

/* Sector bars (TV style — reused from v1 logic) */
.sector-bar-row {
    display: grid;
    grid-template-columns: 5rem 1fr auto 1fr auto 1fr auto;
    align-items: center;
    gap: 3px;
    margin-bottom: 4px;
    font-size: 0.75rem;
}
.sector-label { color: var(--tv-muted); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sector-bar-cell { position: relative; height: 8px; background: rgba(255,255,255,0.06); border-radius: 2px; overflow: hidden; }
.sector-bar { position: absolute; top: 0; height: 100%; border-radius: 2px; }
.sector-bar.positive { left: 50%; background: var(--tv-green); }
.sector-bar.negative { right: 50%; background: var(--tv-red); }
.sector-pct { font-family: 'IBM Plex Mono', monospace; font-size: 9px; text-align: right; min-width: 3rem; }
.sector-pct.positive { color: var(--tv-green); }
.sector-pct.negative { color: var(--tv-red); }
.sector-pct.neutral  { color: var(--tv-muted); }
.sector-timeframe-label { font-size: 9px; font-weight: 600; text-transform: uppercase; color: var(--tv-muted); text-align: center; }

.vix-spot { font-family: 'IBM Plex Mono', monospace; font-size: 22px; font-weight: 700; margin-bottom: 4px; }
.vix-changes { font-size: 10px; color: var(--tv-muted); margin-bottom: 6px; }
.vix-term { font-size: 10px; padding: 2px 6px; border-radius: 4px; display: inline-block; }
.vix-term.contango     { background: rgba(8,153,129,0.15); color: var(--tv-green); }
.vix-term.backwardation{ background: rgba(242,54,69,0.15); color: var(--tv-red); }
.vix-term.flat         { background: rgba(247,201,72,0.15); color: var(--tv-yellow); }

.gex-value { font-family: 'IBM Plex Mono', monospace; font-size: 22px; font-weight: 700; margin-bottom: 4px; }
.gex-label { font-size: 10px; color: var(--tv-muted); margin-bottom: 4px; }
.gex-avg   { font-size: 10px; color: var(--tv-muted); }

.breadth-row { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.breadth-label { min-width: 4rem; color: var(--tv-muted); font-size: 10px; }
.breadth-bar-track { flex: 1; height: 6px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; }
.breadth-bar-fill { height: 100%; border-radius: 3px; transition: width 0.3s ease; }
.breadth-bar-fill.green  { background: var(--tv-green); }
.breadth-bar-fill.yellow { background: var(--tv-yellow); }
.breadth-bar-fill.red    { background: var(--tv-red); }
.breadth-value { min-width: 2.5rem; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 10px; font-weight: 600; }
.breadth-value.green  { color: var(--tv-green); }
.breadth-value.yellow { color: var(--tv-yellow); }
.breadth-value.red    { color: var(--tv-red); }
.breadth-ad { font-size: 10px; color: var(--tv-muted); }

/* ── Handoff view ── */
.handoff-view { padding: 40px 20px; text-align: center; }
.handoff-title { font-size: 18px; font-weight: 600; color: #fff; margin-bottom: 8px; }
.handoff-desc  { font-size: 13px; color: var(--tv-muted); margin-bottom: 24px; line-height: 1.5; }
.handoff-btn {
    display: inline-block;
    padding: 10px 24px;
    border-radius: 8px;
    background: rgba(41,98,255,0.15);
    border: 1px solid rgba(41,98,255,0.4);
    color: #5B8AF5;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    font-weight: 600;
    text-decoration: none;
    transition: background 0.15s, border-color 0.15s;
}
.handoff-btn:hover { background: rgba(41,98,255,0.25); border-color: rgba(41,98,255,0.6); }
```

- [ ] **Step 3: Verify HTML parses** — open browser devtools console, confirm no parse errors. No JS yet.

---

## Task 2: Bottom nav routing skeleton in app.js

**Files:**
- Modify: `src/web/v2/app.js`

- [ ] **Step 1: Replace `DOMContentLoaded` handler and add routing state** — at the top of the state section, add:

```javascript
// ── Routing ───────────────────────────────────────────────────────────────────
let activeTab = 'watchlist';

const NAV_ICONS = {
    overview:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="10" width="4" height="11" rx="1"/><rect x="10" y="4" width="4" height="17" rx="1"/><rect x="17" y="7" width="4" height="14" rx="1"/></svg>`,
    watchlist:  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="18" x2="20" y2="18"/><circle cx="3.5" cy="6" r="1.5" fill="currentColor" stroke="none"/><circle cx="3.5" cy="12" r="1.5" fill="currentColor" stroke="none"/><circle cx="3.5" cy="18" r="1.5" fill="currentColor" stroke="none"/></svg>`,
    scanner:    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="22" y2="22"/></svg>`,
    backtester: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.95"/></svg>`,
};

const NAV_LABELS = {
    overview: 'Overview', watchlist: 'Watchlist',
    scanner: 'Scanner', backtester: 'Backtest',
};

function renderBottomNav() {
    document.getElementById('bottom-nav').innerHTML =
        Object.keys(NAV_ICONS).map(tab =>
            `<button class="nav-item${tab === activeTab ? ' active' : ''}" onclick="switchTab('${tab}')">
                ${NAV_ICONS[tab]}
                <span>${NAV_LABELS[tab]}</span>
            </button>`
        ).join('');
}

function switchTab(tab) {
    activeTab = tab;
    renderBottomNav();
    switch (tab) {
        case 'overview':    renderOverviewView();   break;
        case 'watchlist':   renderWatchlistView();  break;
        case 'scanner':     renderHandoffView('Scanner',    '/scanner.html',  'Universe and watchlist CSP scanning with custom parameters.'); break;
        case 'backtester':  renderHandoffView('Backtester', '/backtest.html', 'Backtest CSP strategies against historical price data.');        break;
    }
}
```

- [ ] **Step 2: Add stub view functions** — add these stubs so the app doesn't crash when tabs are clicked (they'll be filled in subsequent tasks):

```javascript
function renderOverviewView()  { document.getElementById('main-content').innerHTML = '<div class="list-message loading">Loading overview…</div>'; }
function renderWatchlistView() { document.getElementById('main-content').innerHTML = '<div class="list-message loading">Loading watchlist…</div>'; }
function renderHandoffView(label, url, desc) {
    document.getElementById('main-content').innerHTML = `
        <div class="handoff-view">
            <div class="handoff-title">${label}</div>
            <p class="handoff-desc">${desc}</p>
            <a href="${url}" class="handoff-btn">Open ${label} →</a>
        </div>`;
}
```

- [ ] **Step 3: Update `DOMContentLoaded`** — replace the existing handler:

```javascript
document.addEventListener('DOMContentLoaded', () => {
    renderBottomNav();
    fetchMarketPosture(); // always populate status bar pill
    switchTab('watchlist');
});
```

- [ ] **Step 4: Build + smoke test**

```bash
docker compose build dashboard && docker compose up -d dashboard
```

Navigate to `https://dev-mi.austin10berge.com/v2/`. Confirm:
- Bottom nav appears with 4 items
- Watchlist tab is active (blue)
- Other tabs show "Loading…" stubs when clicked
- Status bar posture pill populates

---

## Task 3: Watchlist view — Tickers sub-tab (refactor existing)

**Files:**
- Modify: `src/web/v2/app.js`

The existing Tickers logic (`renderTabs`, `renderColHeaders`, `renderStockCandidates`, `fetchStockScreener`, etc.) is all correct — it just needs to be invoked from inside the Watchlist view rather than from `DOMContentLoaded`.

- [ ] **Step 1: Add Watchlist routing state**

```javascript
// ── Watchlist state ───────────────────────────────────────────────────────────
let activeWatchlistTab = 'tickers';
const watchlistFetched = { tickers: false, csp: false, leaps: false };
```

- [ ] **Step 2: Replace `renderWatchlistView` stub**

```javascript
function renderWatchlistView() {
    document.getElementById('main-content').innerHTML = `
        <div class="section-header" style="padding-bottom:8px">
            <div class="watchlist-sub-tabs" id="watchlist-sub-tabs"></div>
            <span class="cache-badge" id="cache-status-active"></span>
        </div>
        <div id="watchlist-content"></div>`;
    renderWatchlistSubTabs();
    showWatchlistTab(activeWatchlistTab);
}

function renderWatchlistSubTabs() {
    const tabs = [
        { id: 'tickers', label: 'Tickers' },
        { id: 'csp',     label: 'CSP' },
        { id: 'leaps',   label: 'LEAPS' },
    ];
    document.getElementById('watchlist-sub-tabs').innerHTML = tabs.map(t =>
        `<button class="sub-tab${t.id === activeWatchlistTab ? ' active' : ''}" onclick="switchWatchlistTab('${t.id}')">${t.label}</button>`
    ).join('');
}

function switchWatchlistTab(tab) {
    activeWatchlistTab = tab;
    renderWatchlistSubTabs();
    showWatchlistTab(tab);
}

function showWatchlistTab(tab) {
    switch (tab) {
        case 'tickers': renderTickersContent(); break;
        case 'csp':     renderCspContent();     break;
        case 'leaps':   renderLeapsContent();   break;
    }
}
```

- [ ] **Step 3: Add `renderTickersContent()`** — moves the existing DOM setup out of `DOMContentLoaded` into this function; fetches only on first visit:

```javascript
function renderTickersContent() {
    document.getElementById('watchlist-content').innerHTML = `
        <div class="col-tabs" id="col-tabs"></div>
        <div class="col-header-row" id="col-header-row">
            <div></div><div></div><div></div><div></div><div></div>
        </div>
        <div class="ticker-list" id="stocks-list">
            <div class="list-message loading">Loading watchlist…</div>
        </div>`;
    renderTabs();
    renderColHeaders();
    if (!watchlistFetched.tickers) {
        watchlistFetched.tickers = true;
        fetchStockScreener();
    } else {
        renderStockCandidates(sortedCandidates());
        // restore cache badge
        const badge = document.getElementById('cache-status-active');
        if (badge && lastStocksResponse) updateCacheStatusEl(badge, lastStocksResponse);
    }
}
```

- [ ] **Step 4: Cache the last stocks API response** — update `fetchStockScreener` to store the raw response so the badge can be restored on re-visit:

```javascript
let lastStocksResponse = null;

async function fetchStockScreener() {
    try {
        const res = await fetch(`${API_BASE}/screener/stocks`);
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        lastStocksResponse = data;
        allStockCandidates = data.candidates || [];
        const badge = document.getElementById('cache-status-active');
        if (badge) updateCacheStatusEl(badge, data);
        renderStockCandidates(sortedCandidates());
    } catch {
        const el = document.getElementById('stocks-list');
        if (el) el.innerHTML = '<div class="list-message">Error loading watchlist</div>';
    }
}
```

- [ ] **Step 5: Split `updateCacheStatus` into element-based helper** — replace the existing function with one that takes an element directly (so any sub-tab can reuse it), and keep a wrapper for backward compat:

```javascript
function updateCacheStatusEl(el, apiResponse) {
    if (!el) return;
    const cachedAt = apiResponse.cached_at;
    const marketStatus = apiResponse.market_status || '';
    if (!cachedAt) { el.textContent = 'Live'; el.className = 'cache-badge live'; return; }
    const ageMins = Math.round((Date.now() - new Date(cachedAt).getTime()) / 60000);
    const ageLabel = ageMins < 1 ? 'just now' : ageMins < 60 ? `${ageMins}m ago` : `${Math.round(ageMins / 60)}h ago`;
    el.textContent = `${ageLabel} · ${marketStatus}`;
    el.className = `cache-badge ${marketStatus === 'Market Open' ? 'market-open' : 'market-closed'}`;
    el.title = `Cached at ${new Date(cachedAt).toLocaleTimeString()}`;
}

function updateCacheStatus(section, apiResponse) {
    updateCacheStatusEl(document.getElementById(`cache-status-${section}`), apiResponse);
}
```

- [ ] **Step 6: Add stub functions for CSP + LEAPS content** (fills in Tasks 4 and 5):

```javascript
function renderCspContent()   { document.getElementById('watchlist-content').innerHTML = '<div class="list-message loading">Loading CSP…</div>'; }
function renderLeapsContent() { document.getElementById('watchlist-content').innerHTML = '<div class="list-message loading">Loading LEAPS…</div>'; }
```

- [ ] **Step 7: Build + verify**

```bash
docker compose build dashboard && docker compose up -d dashboard
```

Navigate to `https://dev-mi.austin10berge.com/v2/`. Confirm:
- Tickers sub-tab loads and data populates
- Sub-tab bar shows Tickers | CSP | LEAPS
- CSP and LEAPS tabs show loading placeholder
- Switching back to Tickers re-renders immediately from cache (no second fetch)

---

## Task 4: Watchlist — CSP sub-tab

**Files:**
- Modify: `src/web/v2/app.js`

Source data: `GET /api/screener/csp` → `{ candidates: [...], cached_at, market_status }`. Each candidate has: `symbol`, `name`, `current_price`, `strike`, `premium`, `roc_percent`, `annualized_roc`, `otm_percent`, `delta`, `spread_pct`, `impliedVolatility`, `volume`, `dte`, `forward_pe`, `peg_ratio`.

- [ ] **Step 1: Add CSP state**

```javascript
let allCspCandidates = [];
let cspSort = { column: 'annualized_roc', asc: false };
let cspPage = 1;
const CSP_PER_PAGE = 10;
let lastCspResponse = null;
```

- [ ] **Step 2: Replace `renderCspContent` stub**

```javascript
function renderCspContent() {
    document.getElementById('watchlist-content').innerHTML = `
        <div class="col-header-row" style="grid-template-columns:1fr auto auto auto; padding: 4px 14px 6px">
            <div></div>
            <div class="ch-col${cspSort.column==='roc_percent'?' sorted':''}" onclick="sortCspBy('roc_percent')">ROC${cspSort.column==='roc_percent'?` <span class="sort-dir">${cspSort.asc?'↑':'↓'}</span>`:''}</div>
            <div class="ch-col${cspSort.column==='annualized_roc'?' sorted':''}" onclick="sortCspBy('annualized_roc')">Yield${cspSort.column==='annualized_roc'?` <span class="sort-dir">${cspSort.asc?'↑':'↓'}</span>`:''}</div>
            <div class="ch-col${cspSort.column==='dte'?' sorted':''}" onclick="sortCspBy('dte')">DTE${cspSort.column==='dte'?` <span class="sort-dir">${cspSort.asc?'↑':'↓'}</span>`:''}</div>
        </div>
        <div id="csp-list"><div class="list-message loading">Loading CSP…</div></div>
        <div class="option-pagination" id="csp-pagination" style="display:none"></div>`;
    if (!watchlistFetched.csp) {
        watchlistFetched.csp = true;
        fetchCspCandidates();
    } else {
        renderCspPage();
        const badge = document.getElementById('cache-status-active');
        if (badge && lastCspResponse) updateCacheStatusEl(badge, lastCspResponse);
    }
}
```

- [ ] **Step 3: Add CSP fetch**

```javascript
async function fetchCspCandidates() {
    try {
        const res = await fetch(`${API_BASE}/screener/csp`);
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        lastCspResponse = data;
        allCspCandidates = data.candidates || [];
        const badge = document.getElementById('cache-status-active');
        if (badge) updateCacheStatusEl(badge, data);
        cspPage = 1;
        renderCspPage();
    } catch {
        const el = document.getElementById('csp-list');
        if (el) el.innerHTML = '<div class="list-message">Error loading CSP data</div>';
    }
}
```

- [ ] **Step 4: Add CSP sort + render helpers**

```javascript
function sortCspBy(col) {
    cspSort.asc = cspSort.column === col ? !cspSort.asc : false;
    cspSort.column = col;
    cspPage = 1;
    renderCspContent(); // re-render headers + list
}

function sortedCsp() {
    return [...allCspCandidates].sort((a, b) => {
        const vA = parseFloat(a[cspSort.column]) || 0;
        const vB = parseFloat(b[cspSort.column]) || 0;
        return cspSort.asc ? vA - vB : vB - vA;
    });
}

function renderCspPage() {
    const sorted = sortedCsp();
    const totalPages = Math.max(1, Math.ceil(sorted.length / CSP_PER_PAGE));
    cspPage = Math.min(cspPage, totalPages);
    const slice = sorted.slice((cspPage - 1) * CSP_PER_PAGE, cspPage * CSP_PER_PAGE);

    const listEl = document.getElementById('csp-list');
    const pagEl  = document.getElementById('csp-pagination');
    if (!listEl) return;

    if (slice.length === 0) {
        listEl.innerHTML = '<div class="list-message">No CSP candidates found</div>';
        if (pagEl) pagEl.style.display = 'none';
        return;
    }

    listEl.innerHTML = slice.map((c, i) => {
        const roc = parseFloat(c.roc_percent) || 0;
        const yld = c.annualized_roc ? `${c.annualized_roc}% yield` : '—';
        return `<div class="option-card" style="--row-delay:${i * 20}ms" onclick="openTradingView('${escHtml(c.symbol)}')">
            <div class="oc-row1">
                <span class="oc-symbol">${escHtml(c.symbol)}</span>
                <span class="oc-meta">$${c.strike.toFixed(2)} · ${c.dte ?? '—'}d · Δ${c.delta != null ? c.delta.toFixed(2) : '—'}</span>
                <span class="oc-highlight">${roc.toFixed(2)}% ROC</span>
            </div>
            <div class="oc-row2">
                <span class="oc-name">${escHtml(c.name)} · $${c.current_price.toFixed(2)}</span>
                <span class="oc-metrics">$${c.premium.toFixed(2)} prem · ${yld} · ${c.otm_percent}% OTM</span>
            </div>
        </div>`;
    }).join('');

    if (pagEl) {
        pagEl.style.display = totalPages > 1 ? 'flex' : 'none';
        pagEl.innerHTML = `
            <button class="page-btn" onclick="stepCspPage(-1)" ${cspPage <= 1 ? 'disabled' : ''}>←</button>
            <span class="page-info-text">${cspPage} / ${totalPages}</span>
            <button class="page-btn" onclick="stepCspPage(1)" ${cspPage >= totalPages ? 'disabled' : ''}>→</button>`;
    }
}

function stepCspPage(dir) {
    cspPage += dir;
    renderCspPage();
}
```

- [ ] **Step 5: Build + verify CSP tab**

```bash
docker compose build dashboard && docker compose up -d dashboard
```

Navigate to `https://dev-mi.austin10berge.com/v2/`, click Watchlist → CSP. Confirm:
- Cards render with symbol, strike/DTE/delta, ROC, premium, yield, OTM%
- Pagination appears when > 10 results
- Clicking a card opens TradingView
- Sort headers (ROC, Yield, DTE) work; active column shows direction arrow

---

## Task 5: Watchlist — LEAPS sub-tab

**Files:**
- Modify: `src/web/v2/app.js`

Source data: `GET /api/screener/leaps` → `{ candidates: [...], cached_at, market_status }`. Each candidate has: `symbol`, `name`, `current_price`, `strike`, `premium`, `premium_markup_percent`, `volume`, `expiration`, `break_even`.

- [ ] **Step 1: Add LEAPS state**

```javascript
let allLeapsCandidates = [];
let leapsSort = { column: 'premium_markup_percent', asc: true };
let leapsPage = 1;
const LEAPS_PER_PAGE = 10;
let lastLeapsResponse = null;
```

- [ ] **Step 2: Replace `renderLeapsContent` stub**

```javascript
function renderLeapsContent() {
    document.getElementById('watchlist-content').innerHTML = `
        <div class="col-header-row" style="grid-template-columns:1fr auto auto auto; padding: 4px 14px 6px">
            <div></div>
            <div class="ch-col${leapsSort.column==='premium_markup_percent'?' sorted':''}" onclick="sortLeapsBy('premium_markup_percent')">Markup${leapsSort.column==='premium_markup_percent'?` <span class="sort-dir">${leapsSort.asc?'↑':'↓'}</span>`:''}</div>
            <div class="ch-col${leapsSort.column==='premium'?' sorted':''}" onclick="sortLeapsBy('premium')">Premium${leapsSort.column==='premium'?` <span class="sort-dir">${leapsSort.asc?'↑':'↓'}</span>`:''}</div>
            <div class="ch-col${leapsSort.column==='expiration'?' sorted':''}" onclick="sortLeapsBy('expiration')">Expiry${leapsSort.column==='expiration'?` <span class="sort-dir">${leapsSort.asc?'↑':'↓'}</span>`:''}</div>
        </div>
        <div id="leaps-list"><div class="list-message loading">Loading LEAPS…</div></div>
        <div class="option-pagination" id="leaps-pagination" style="display:none"></div>`;
    if (!watchlistFetched.leaps) {
        watchlistFetched.leaps = true;
        fetchLeapsCandidates();
    } else {
        renderLeapsPage();
        const badge = document.getElementById('cache-status-active');
        if (badge && lastLeapsResponse) updateCacheStatusEl(badge, lastLeapsResponse);
    }
}
```

- [ ] **Step 3: Add LEAPS fetch**

```javascript
async function fetchLeapsCandidates() {
    try {
        const res = await fetch(`${API_BASE}/screener/leaps`);
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        lastLeapsResponse = data;
        allLeapsCandidates = data.candidates || [];
        const badge = document.getElementById('cache-status-active');
        if (badge) updateCacheStatusEl(badge, data);
        leapsPage = 1;
        renderLeapsPage();
    } catch {
        const el = document.getElementById('leaps-list');
        if (el) el.innerHTML = '<div class="list-message">Error loading LEAPS data</div>';
    }
}
```

- [ ] **Step 4: Add LEAPS sort + render helpers**

```javascript
function sortLeapsBy(col) {
    leapsSort.asc = leapsSort.column === col ? !leapsSort.asc : (col === 'expiration');
    leapsSort.column = col;
    leapsPage = 1;
    renderLeapsContent();
}

function sortedLeaps() {
    return [...allLeapsCandidates].sort((a, b) => {
        const vA = a[leapsSort.column], vB = b[leapsSort.column];
        if (typeof vA === 'string') {
            return leapsSort.asc ? vA.localeCompare(vB) : vB.localeCompare(vA);
        }
        return leapsSort.asc ? vA - vB : vB - vA;
    });
}

function renderLeapsPage() {
    const sorted = sortedLeaps();
    const totalPages = Math.max(1, Math.ceil(sorted.length / LEAPS_PER_PAGE));
    leapsPage = Math.min(leapsPage, totalPages);
    const slice = sorted.slice((leapsPage - 1) * LEAPS_PER_PAGE, leapsPage * LEAPS_PER_PAGE);

    const listEl = document.getElementById('leaps-list');
    const pagEl  = document.getElementById('leaps-pagination');
    if (!listEl) return;

    if (slice.length === 0) {
        listEl.innerHTML = '<div class="list-message">No LEAPS candidates found</div>';
        if (pagEl) pagEl.style.display = 'none';
        return;
    }

    listEl.innerHTML = slice.map((c, i) =>
        `<div class="option-card" style="--row-delay:${i * 20}ms" onclick="openTradingView('${escHtml(c.symbol)}')">
            <div class="oc-row1">
                <span class="oc-symbol">${escHtml(c.symbol)}</span>
                <span class="oc-meta">$${c.strike.toFixed(2)} · ${c.expiration}</span>
                <span class="oc-highlight">${c.premium_markup_percent.toFixed(1)}% mkup</span>
            </div>
            <div class="oc-row2">
                <span class="oc-name">${escHtml(c.name)} · $${c.current_price.toFixed(2)}</span>
                <span class="oc-metrics">$${c.premium.toFixed(2)} prem · BE $${c.break_even.toFixed(2)}</span>
            </div>
        </div>`
    ).join('');

    if (pagEl) {
        pagEl.style.display = totalPages > 1 ? 'flex' : 'none';
        pagEl.innerHTML = `
            <button class="page-btn" onclick="stepLeapsPage(-1)" ${leapsPage <= 1 ? 'disabled' : ''}>←</button>
            <span class="page-info-text">${leapsPage} / ${totalPages}</span>
            <button class="page-btn" onclick="stepLeapsPage(1)" ${leapsPage >= totalPages ? 'disabled' : ''}>→</button>`;
    }
}

function stepLeapsPage(dir) {
    leapsPage += dir;
    renderLeapsPage();
}
```

- [ ] **Step 5: Build + verify LEAPS tab**

```bash
docker compose build dashboard && docker compose up -d dashboard
```

Navigate to `https://dev-mi.austin10berge.com/v2/`, click Watchlist → LEAPS. Confirm:
- Cards render with symbol, strike/expiry, markup%, premium, break-even
- Sorted by markup ascending by default
- Pagination appears when > 10 results
- Sort headers work

---

## Task 6: Market Overview view

**Files:**
- Modify: `src/web/v2/app.js`

Source: `GET /api/market-posture` → `{ posture, composite_score, llm_summary, ... }`. `GET /api/market-overview` → `{ sectors: {...}, vix: {...}, gex: {...}, breadth: {...} }`.

- [ ] **Step 1: Add overview state**

```javascript
let cachedPostureData = null;
let cachedOverviewData = null;
let overviewFetched = false;
```

- [ ] **Step 2: Update `fetchMarketPosture` to cache data**

```javascript
async function fetchMarketPosture() {
    try {
        const res = await fetch(`${API_BASE}/market-posture`);
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        cachedPostureData = data;
        renderPosture(data);
        // If overview is currently visible, populate the AI section
        if (activeTab === 'overview') renderOverviewPostureSection(data);
    } catch {
        const el = document.getElementById('posture-widget');
        el.className = 'posture-pill neutral';
        el.innerHTML = '<span class="pulse-dot"></span>Unavailable';
    }
}
```

- [ ] **Step 3: Replace `renderOverviewView` stub**

```javascript
function renderOverviewView() {
    document.getElementById('main-content').innerHTML = `
        <div class="section-header"><span class="section-title">Market Overview</span></div>
        <div class="overview-section">
            <div class="overview-card" id="posture-detail-card">
                <div class="overview-card-title">Market Posture</div>
                <div id="posture-detail"><div class="list-message loading" style="padding:8px 0">Loading…</div></div>
            </div>
            <div class="overview-card" id="llm-card">
                <div class="overview-card-title">AI Synthesis</div>
                <div id="llm-summary" class="llm-text"><span class="list-message loading" style="display:inline">Loading…</span></div>
            </div>
            <div class="overview-grid">
                <div class="overview-card"><div class="overview-card-title">Sectors</div><div id="sector-bars"><span class="list-message loading" style="display:inline">…</span></div></div>
                <div class="overview-card"><div class="overview-card-title">VIX</div><div id="vix-content"><span class="list-message loading" style="display:inline">…</span></div></div>
                <div class="overview-card"><div class="overview-card-title">GEX</div><div id="gex-content"><span class="list-message loading" style="display:inline">…</span></div></div>
                <div class="overview-card"><div class="overview-card-title">Breadth</div><div id="breadth-content"><span class="list-message loading" style="display:inline">…</span></div></div>
            </div>
        </div>`;

    // Populate from cache if available
    if (cachedPostureData) renderOverviewPostureSection(cachedPostureData);
    if (cachedOverviewData) {
        renderSectors(cachedOverviewData.sectors);
        renderVix(cachedOverviewData.vix);
        renderGex(cachedOverviewData.gex);
        renderBreadth(cachedOverviewData.breadth);
    }
    if (!overviewFetched) {
        overviewFetched = true;
        fetchMarketOverview();
    }
}

function renderOverviewPostureSection(data) {
    const detailEl = document.getElementById('posture-detail');
    const llmEl    = document.getElementById('llm-summary');
    if (detailEl) {
        const txt = data.posture || 'Neutral';
        const cls = txt.includes('Bullish') ? 'positive' : txt.includes('Bearish') ? 'negative' : '';
        const s   = parseFloat(data.composite_score).toFixed(3);
        detailEl.innerHTML = `
            <div class="posture-detail-score ${cls}">${txt}</div>
            <div class="posture-detail-label">Composite signal: ${s > 0 ? '+' : ''}${s}</div>`;
    }
    if (llmEl && data.llm_summary) {
        let html = escHtml(data.llm_summary);
        ['POSTURE', 'THETA PLAY', 'WATCHLIST'].forEach(label => {
            html = html.replace(new RegExp(`(${label}:)`, 'g'), '<strong>$1</strong>');
        });
        llmEl.innerHTML = html.replace(/\n/g, '<br>');
    }
}
```

- [ ] **Step 4: Add `fetchMarketOverview` and all render helpers** — port directly from `src/web/app.js`, updating only element IDs (they match):

```javascript
async function fetchMarketOverview() {
    try {
        const res = await fetch(`${API_BASE}/market-overview`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        cachedOverviewData = data;
        renderSectors(data.sectors);
        renderVix(data.vix);
        renderGex(data.gex);
        renderBreadth(data.breadth);
    } catch (err) {
        console.error('fetchMarketOverview failed:', err);
        ['sector-bars', 'vix-content', 'gex-content', 'breadth-content'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size:11px">Unavailable</span>';
        });
    }
}

function renderSectors(sectors) {
    const el = document.getElementById('sector-bars');
    if (!el || !sectors) { if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size:11px">Unavailable</span>'; return; }
    const sorted = Object.entries(sectors).sort(([,a],[,b]) => (b.pct_1d ?? -Infinity) - (a.pct_1d ?? -Infinity));
    const vals1d = sorted.map(([,s]) => s.pct_1d).filter(v => v != null);
    const vals1w = sorted.map(([,s]) => s.pct_1w).filter(v => v != null);
    const max1d = vals1d.length ? Math.max(...vals1d.map(Math.abs)) : 1;
    const max1w = vals1w.length ? Math.max(...vals1w.map(Math.abs)) : 1;
    const barW = (pct, maxAbs) => pct == null || maxAbs === 0 ? 0 : Math.abs(pct) / maxAbs * 50;
    const cls  = pct => pct == null ? 'neutral' : pct >= 0 ? 'positive' : 'negative';
    const fmt  = pct => pct == null ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
    el.innerHTML = `
        <div class="sector-bar-row sector-bar-header">
            <span></span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1D</span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1W</span>
        </div>` +
        sorted.map(([ticker, s]) => `
        <div class="sector-bar-row">
            <span class="sector-label" title="${escHtml(ticker)}">${escHtml(s.name)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1d)}" style="width:${barW(s.pct_1d,max1d)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1d)}">${fmt(s.pct_1d)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1w)}" style="width:${barW(s.pct_1w,max1w)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1w)}">${fmt(s.pct_1w)}</span>
        </div>`).join('');
}

function renderVix(vix) {
    const el = document.getElementById('vix-content');
    if (!el) return;
    if (!vix || vix.spot == null) { el.innerHTML = '<span style="color:var(--tv-muted);font-size:11px">Unavailable</span>'; return; }
    const fmtChg = pct => {
        if (pct == null) return '<span style="color:var(--tv-muted)">—</span>';
        const color = pct >= 0 ? 'var(--tv-green)' : 'var(--tv-red)';
        return `<span style="color:${color}">${pct >= 0 ? '↑' : '↓'}${Math.abs(pct).toFixed(1)}%</span>`;
    };
    const tsClass = { Contango: 'contango', Backwardation: 'backwardation', Flat: 'flat' }[vix.term_structure] ?? 'flat';
    el.innerHTML = `
        <div class="vix-spot">${vix.spot.toFixed(2)}</div>
        <div class="vix-changes">1D: ${fmtChg(vix.pct_1d)} &nbsp; 1W: ${fmtChg(vix.pct_1w)}</div>
        <div class="vix-term ${tsClass}">${escHtml(vix.term_structure)} — ${escHtml(vix.stress_note)} (spread ${vix.spread >= 0 ? '+' : ''}${vix.spread.toFixed(2)})</div>`;
}

function renderGex(gex) {
    const el = document.getElementById('gex-content');
    if (!el) return;
    if (!gex || gex.value_b == null) { el.innerHTML = '<span style="color:var(--tv-muted);font-size:11px">Unavailable</span>'; return; }
    const arrow = gex.trend === 'Rising' ? '↑' : gex.trend === 'Falling' ? '↓' : '→';
    el.innerHTML = `
        <div class="gex-value">$${gex.value_b.toFixed(1)}B</div>
        <div class="gex-label">${escHtml(gex.label)}</div>
        <div class="gex-avg">20d avg: $${gex.rolling_20d_avg_b.toFixed(1)}B &nbsp; ${arrow} ${escHtml(gex.trend)}</div>`;
}

function renderBreadth(breadth) {
    const el = document.getElementById('breadth-content');
    if (!el) return;
    if (!breadth) { el.innerHTML = '<span style="color:var(--tv-muted);font-size:11px">Unavailable</span>'; return; }
    const pct = breadth.pct_above_200ma ?? 0;
    const maColor = pct >= 60 ? 'green' : pct >= 40 ? 'yellow' : 'red';
    const adColor = (breadth.ad_ratio ?? 0) >= 1.2 ? 'green' : (breadth.ad_ratio ?? 0) >= 0.8 ? 'yellow' : 'red';
    el.innerHTML = `
        <div class="breadth-row">
            <span class="breadth-label">200d MA</span>
            <div class="breadth-bar-track"><div class="breadth-bar-fill ${maColor}" style="width:${pct.toFixed(1)}%"></div></div>
            <span class="breadth-value ${maColor}">${pct.toFixed(0)}%</span>
        </div>
        <div class="breadth-ad">A/D &nbsp; ${breadth.advancing}↑ / ${breadth.declining}↓ &nbsp; ratio ${breadth.ad_ratio != null ? breadth.ad_ratio.toFixed(2) : '—'}</div>`;
}
```

- [ ] **Step 5: Build + verify Overview tab**

```bash
docker compose build dashboard && docker compose up -d dashboard
```

Navigate to `https://dev-mi.austin10berge.com/v2/`, click Overview. Confirm:
- Posture card shows label + composite score
- AI synthesis section shows LLM summary text
- Sectors panel shows bar chart
- VIX, GEX, Breadth panels populate
- Switching away and back uses cached data (no second fetch)

---

## Task 7: Final integration — build, verify all tabs, commit

- [ ] **Step 1: Full rebuild**

```bash
docker compose build dashboard && docker compose up -d dashboard
```

- [ ] **Step 2: Verify each tab in browser at `https://dev-mi.austin10berge.com/v2/`**

| Check | Expected |
|-------|----------|
| Overview tab | Posture + AI synthesis + 4 data panels |
| Watchlist → Tickers | Column-switcher list, sort works |
| Watchlist → CSP | Cards with ROC/yield/DTE, pagination |
| Watchlist → LEAPS | Cards with markup/premium/BE, pagination |
| Scanner tab | Handoff card with "Open Scanner →" link |
| Backtester tab | Handoff card with "Open Backtester →" link |
| Scanner link | Navigates to `/scanner.html` |
| Backtester link | Navigates to `/backtest.html` |
| v1 at `/` | Unchanged |
| Status bar | Posture pill visible on all tabs |
| Re-visit Tickers | No second API call (uses cache) |

- [ ] **Step 3: Commit**

```bash
git add src/web/v2/index.html src/web/v2/app.js
git commit -m "feat: v2 SPA navigation — bottom nav, Overview, Watchlist (Tickers/CSP/LEAPS), Scanner/Backtester handoff"
```
