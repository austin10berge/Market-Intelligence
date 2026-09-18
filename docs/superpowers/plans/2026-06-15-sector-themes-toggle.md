# Sector / Themes Toggle — Overview Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the Sector card to full width on the Overview tab, add a pill toggle to switch between Sector ETFs and custom Themes, and extend return columns to 1D + 1W + 1M in both views.

**Architecture:** Pure frontend changes to two files — `index.html` (CSS) and `app.js` (JS). The API already returns `themes` data alongside `sectors` in `/api/market-overview`; no backend changes needed. The sectors card is moved out of the 2-column `overview-grid` and placed above it. A `sectorView` state variable drives which render function (`renderSectors` or `renderThemes`) is called.

**Tech Stack:** Vanilla JS, CSS grid, Playwright MCP for visual verification.

---

## File Map

| File | What changes |
|------|-------------|
| `src/web/v2/index.html` | CSS: `.sector-bar-row` grid template (5→7 cols), pill toggle styles, basket expand styles |
| `src/web/v2/app.js` | Add `sectorView` state; rewrite `renderOverviewView()`; update `renderSectors()` (add 1M); add `renderThemes()`; add `switchSectorView()`; update `fetchMarketOverview()` |

---

## Task 1: CSS — Update sector grid and add toggle/basket styles

**Files:**
- Modify: `src/web/v2/index.html` (around line 540–557)

- [ ] **Step 1: Replace the `.sector-bar-row` rule and add new styles**

Find this block (around line 540):
```css
        /* Sector bars */
        .sector-bar-row {
            display: grid;
            grid-template-columns: 5rem 1fr auto 1fr auto;
            align-items: center;
            gap: 3px;
            margin-bottom: 4px;
            font-size: 0.9rem;
        }
        .sector-label { color: var(--tv-muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .sector-bar-cell { position: relative; height: 8px; background: rgba(255,255,255,0.06); border-radius: 2px; overflow: hidden; }
        .sector-bar { position: absolute; top: 0; height: 100%; border-radius: 2px; }
        .sector-bar.positive { left: 50%; background: var(--tv-green); }
        .sector-bar.negative { right: 50%; background: var(--tv-red); }
        .sector-pct { font-family: 'IBM Plex Mono', monospace; font-size: 11px; text-align: right; min-width: 3rem; }
        .sector-pct.positive { color: var(--tv-green); }
        .sector-pct.negative { color: var(--tv-red); }
        .sector-pct.neutral  { color: var(--tv-muted); }
        .sector-timeframe-label { font-size: 11px; font-weight: 600; text-transform: uppercase; color: var(--tv-muted); text-align: center; }
```

Replace it with:
```css
        /* Sector bars */
        .sector-bar-row {
            display: grid;
            grid-template-columns: 5rem 1fr auto 1fr auto 1fr auto;
            align-items: center;
            gap: 3px;
            margin-bottom: 4px;
            font-size: 0.9rem;
        }
        .sector-label { color: var(--tv-muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .sector-bar-cell { position: relative; height: 8px; background: rgba(255,255,255,0.06); border-radius: 2px; overflow: hidden; }
        .sector-bar { position: absolute; top: 0; height: 100%; border-radius: 2px; }
        .sector-bar.positive { left: 50%; background: var(--tv-green); }
        .sector-bar.negative { right: 50%; background: var(--tv-red); }
        .sector-pct { font-family: 'IBM Plex Mono', monospace; font-size: 11px; text-align: right; min-width: 3rem; }
        .sector-pct.positive { color: var(--tv-green); }
        .sector-pct.negative { color: var(--tv-red); }
        .sector-pct.neutral  { color: var(--tv-muted); }
        .sector-timeframe-label { font-size: 11px; font-weight: 600; text-transform: uppercase; color: var(--tv-muted); text-align: center; }

        /* Sector card header with toggle */
        .sector-card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 8px;
        }
        .sector-card-header .overview-card-title { margin-bottom: 0; }
        .sector-toggle {
            display: flex;
            background: var(--tv-surface2);
            border-radius: 6px;
            padding: 2px;
            gap: 2px;
        }
        .sector-toggle-btn {
            background: transparent;
            border: none;
            color: var(--tv-muted);
            font-family: 'IBM Plex Mono', monospace;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.15s, color 0.15s;
        }
        .sector-toggle-btn.active {
            background: var(--tv-text);
            color: var(--tv-bg);
        }

        /* Basket expand/collapse */
        .basket-row { cursor: pointer; }
        .basket-row:hover { background: rgba(255,255,255,0.03); border-radius: 4px; }
        .basket-chevron {
            display: inline-block;
            font-size: 8px;
            margin-right: 4px;
            color: var(--tv-muted);
            transition: transform 0.15s;
            line-height: 1;
        }
        .theme-basket-label { display: flex; align-items: center; }
        .basket-sub-row { background: rgba(255,255,255,0.02); border-radius: 2px; }
```

- [ ] **Step 2: Visual sanity check (no JS changes yet)**

Open `https://dev-mi.austin10berge.com` and confirm the Overview tab still loads without visual breakage. (The sector bars will be misaligned until Task 3 adds the 1M column — that's expected.)

---

## Task 2: JS — Add `sectorView` state and rewrite `renderOverviewView()`

**Files:**
- Modify: `src/web/v2/app.js`

- [ ] **Step 1: Add `sectorView` state variable**

Find the overview state block (around line 88):
```javascript
// ── Overview state ────────────────────────────────────────────────────────────

let cachedPostureData = null;
let cachedOverviewData = null;
let overviewFetched = false;
```

Replace with:
```javascript
// ── Overview state ────────────────────────────────────────────────────────────

let cachedPostureData  = null;
let cachedOverviewData = null;
let overviewFetched    = false;
let sectorView         = 'etfs';
```

- [ ] **Step 2: Rewrite `renderOverviewView()`**

Find the full `renderOverviewView` function (lines 1004–1035):
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
```

Replace with:
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
            <div class="overview-card" id="sector-card">
                <div class="sector-card-header">
                    <div class="overview-card-title">Sectors</div>
                    <div class="sector-toggle">
                        <button class="sector-toggle-btn active" id="toggle-sector-etfs" onclick="switchSectorView('etfs')">Sector ETFs</button>
                        <button class="sector-toggle-btn" id="toggle-themes" onclick="switchSectorView('themes')">Themes</button>
                    </div>
                </div>
                <div id="sector-bars"><span class="list-message loading" style="display:inline">…</span></div>
            </div>
            <div class="overview-grid">
                <div class="overview-card"><div class="overview-card-title">VIX</div><div id="vix-content"><span class="list-message loading" style="display:inline">…</span></div></div>
                <div class="overview-card"><div class="overview-card-title">GEX</div><div id="gex-content"><span class="list-message loading" style="display:inline">…</span></div></div>
                <div class="overview-card"><div class="overview-card-title">Breadth</div><div id="breadth-content"><span class="list-message loading" style="display:inline">…</span></div></div>
            </div>
        </div>`;

    if (cachedPostureData) renderOverviewPostureSection(cachedPostureData);
    if (cachedOverviewData) {
        if (sectorView === 'etfs') renderSectors(cachedOverviewData.sectors);
        else renderThemes(cachedOverviewData.themes);
        renderVix(cachedOverviewData.vix);
        renderGex(cachedOverviewData.gex);
        renderBreadth(cachedOverviewData.breadth);
    }
    if (!overviewFetched) {
        overviewFetched = true;
        fetchMarketOverview();
    }
}
```

- [ ] **Step 3: Add `switchSectorView()` function**

Insert this function directly after `renderOverviewView()` (before `renderOverviewPostureSection`):
```javascript
function switchSectorView(view) {
    sectorView = view;
    const etfsBtn   = document.getElementById('toggle-sector-etfs');
    const themesBtn = document.getElementById('toggle-themes');
    if (etfsBtn)   etfsBtn.classList.toggle('active', view === 'etfs');
    if (themesBtn) themesBtn.classList.toggle('active', view === 'themes');
    if (view === 'etfs') {
        renderSectors(cachedOverviewData?.sectors);
    } else {
        renderThemes(cachedOverviewData?.themes);
    }
}
```

---

## Task 3: JS — Add 1M column to `renderSectors()`

**Files:**
- Modify: `src/web/v2/app.js` (around line 1184)

- [ ] **Step 1: Replace `renderSectors()`**

Find the full function:
```javascript
function renderSectors(sectors) {
    const el = document.getElementById('sector-bars');
    if (!el || !sectors) { if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const sorted = Object.entries(sectors).sort(([, a], [, b]) => (b.pct_1d ?? -Infinity) - (a.pct_1d ?? -Infinity));
    const vals1d = sorted.map(([, s]) => s.pct_1d).filter(v => v != null);
    const vals1w = sorted.map(([, s]) => s.pct_1w).filter(v => v != null);
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
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1d)}" style="width:${barW(s.pct_1d, max1d)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1d)}">${fmt(s.pct_1d)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1w)}" style="width:${barW(s.pct_1w, max1w)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1w)}">${fmt(s.pct_1w)}</span>
        </div>`).join('');
}
```

Replace with:
```javascript
function renderSectors(sectors) {
    const el = document.getElementById('sector-bars');
    if (!el || !sectors) { if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const sorted = Object.entries(sectors).sort(([, a], [, b]) => (b.pct_1d ?? -Infinity) - (a.pct_1d ?? -Infinity));
    const vals1d = sorted.map(([, s]) => s.pct_1d).filter(v => v != null);
    const vals1w = sorted.map(([, s]) => s.pct_1w).filter(v => v != null);
    const vals1m = sorted.map(([, s]) => s.pct_1m).filter(v => v != null);
    const max1d = vals1d.length ? Math.max(...vals1d.map(Math.abs)) : 1;
    const max1w = vals1w.length ? Math.max(...vals1w.map(Math.abs)) : 1;
    const max1m = vals1m.length ? Math.max(...vals1m.map(Math.abs)) : 1;
    const barW = (pct, maxAbs) => pct == null || maxAbs === 0 ? 0 : Math.abs(pct) / maxAbs * 50;
    const cls  = pct => pct == null ? 'neutral' : pct >= 0 ? 'positive' : 'negative';
    const fmt  = pct => pct == null ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;
    el.innerHTML = `
        <div class="sector-bar-row sector-bar-header">
            <span></span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1D</span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1W</span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1M</span>
        </div>` +
        sorted.map(([ticker, s]) => `
        <div class="sector-bar-row">
            <span class="sector-label" title="${escHtml(ticker)}">${escHtml(s.name)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1d)}" style="width:${barW(s.pct_1d, max1d)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1d)}">${fmt(s.pct_1d)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1w)}" style="width:${barW(s.pct_1w, max1w)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1w)}">${fmt(s.pct_1w)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(s.pct_1m)}" style="width:${barW(s.pct_1m, max1m)}%"></div></div>
            <span class="sector-pct ${cls(s.pct_1m)}">${fmt(s.pct_1m)}</span>
        </div>`).join('');
}
```

---

## Task 4: JS — Add `renderThemes()` function

**Files:**
- Modify: `src/web/v2/app.js` (insert after `renderSectors`, before `renderVix`)

- [ ] **Step 1: Insert `renderThemes()` after `renderSectors()`**

Find the line immediately after the closing `}` of `renderSectors` (which is followed by `function renderVix`). Insert the following new function between them:

```javascript
function renderThemes(themes) {
    const el = document.getElementById('sector-bars');
    if (!el || !themes) { if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }

    const { singles = {}, baskets = {} } = themes;

    // Build unified list: singles and baskets share the same row shape
    const items = [];
    for (const [label, d] of Object.entries(singles)) {
        items.push({ label, type: 'single', ticker: d.ticker, pct_1d: d.pct_1d, pct_1w: d.pct_1w, pct_1m: d.pct_1m });
    }
    for (const [label, d] of Object.entries(baskets)) {
        items.push({ label, type: 'basket', tickers: d.tickers, pct_1d: d.avg_1d, pct_1w: d.avg_1w, pct_1m: d.avg_1m });
    }

    // Sort by 1D descending; nulls sort to bottom
    items.sort((a, b) => (b.pct_1d ?? -Infinity) - (a.pct_1d ?? -Infinity));

    const all1d = items.map(i => i.pct_1d).filter(v => v != null);
    const all1w = items.map(i => i.pct_1w).filter(v => v != null);
    const all1m = items.map(i => i.pct_1m).filter(v => v != null);
    const max1d = all1d.length ? Math.max(...all1d.map(Math.abs)) : 1;
    const max1w = all1w.length ? Math.max(...all1w.map(Math.abs)) : 1;
    const max1m = all1m.length ? Math.max(...all1m.map(Math.abs)) : 1;
    const barW = (pct, maxAbs) => pct == null || maxAbs === 0 ? 0 : Math.abs(pct) / maxAbs * 50;
    const cls  = pct => pct == null ? 'neutral' : pct >= 0 ? 'positive' : 'negative';
    const fmt  = pct => pct == null ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`;

    const rows = items.map(item => {
        const nameCell = item.type === 'single'
            ? `<div class="sector-label"><div>${escHtml(item.label)}</div><div style="font-size:10px;color:var(--tv-muted)">${escHtml(item.ticker)}</div></div>`
            : `<div class="sector-label theme-basket-label"><span class="basket-chevron">▶</span>${escHtml(item.label)}</div>`;

        const mainRow = `<div class="sector-bar-row${item.type === 'basket' ? ' basket-row' : ''}" data-basket="${item.type === 'basket' ? escHtml(item.label) : ''}">
            ${nameCell}
            <div class="sector-bar-cell"><div class="sector-bar ${cls(item.pct_1d)}" style="width:${barW(item.pct_1d, max1d)}%"></div></div>
            <span class="sector-pct ${cls(item.pct_1d)}">${fmt(item.pct_1d)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(item.pct_1w)}" style="width:${barW(item.pct_1w, max1w)}%"></div></div>
            <span class="sector-pct ${cls(item.pct_1w)}">${fmt(item.pct_1w)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(item.pct_1m)}" style="width:${barW(item.pct_1m, max1m)}%"></div></div>
            <span class="sector-pct ${cls(item.pct_1m)}">${fmt(item.pct_1m)}</span>
        </div>`;

        if (item.type !== 'basket') return mainRow;

        // Constituent sub-rows (hidden by default)
        const subRows = Object.entries(item.tickers).map(([t, d]) => `
        <div class="sector-bar-row basket-sub-row" data-parent="${escHtml(item.label)}" style="display:none">
            <span class="sector-label" style="font-size:10px;padding-left:16px">${escHtml(t)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(d.pct_1d)}" style="width:${barW(d.pct_1d, max1d)}%"></div></div>
            <span class="sector-pct ${cls(d.pct_1d)}" style="font-size:10px">${fmt(d.pct_1d)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(d.pct_1w)}" style="width:${barW(d.pct_1w, max1w)}%"></div></div>
            <span class="sector-pct ${cls(d.pct_1w)}" style="font-size:10px">${fmt(d.pct_1w)}</span>
            <div class="sector-bar-cell"><div class="sector-bar ${cls(d.pct_1m)}" style="width:${barW(d.pct_1m, max1m)}%"></div></div>
            <span class="sector-pct ${cls(d.pct_1m)}" style="font-size:10px">${fmt(d.pct_1m)}</span>
        </div>`).join('');

        return mainRow + subRows;
    });

    el.innerHTML = `
        <div class="sector-bar-row sector-bar-header">
            <span></span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1D</span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1W</span>
            <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1M</span>
        </div>` + rows.join('');

    // Wire up basket expand/collapse
    el.querySelectorAll('.basket-row').forEach(row => {
        row.addEventListener('click', () => {
            const label   = row.dataset.basket;
            const chevron = row.querySelector('.basket-chevron');
            const subRows = el.querySelectorAll(`.basket-sub-row[data-parent="${label}"]`);
            const isOpen  = subRows.length > 0 && subRows[0].style.display !== 'none';
            subRows.forEach(r => r.style.display = isOpen ? 'none' : 'grid');
            if (chevron) chevron.textContent = isOpen ? '▶' : '▼';
        });
    });
}
```

---

## Task 5: JS — Update `fetchMarketOverview()` and verify

**Files:**
- Modify: `src/web/v2/app.js` (around line 1165)

- [ ] **Step 1: Update `fetchMarketOverview()` to respect `sectorView`**

Find:
```javascript
        cachedOverviewData = data;
        renderSectors(data.sectors);
        renderVix(data.vix);
        renderGex(data.gex);
        renderBreadth(data.breadth);
```

Replace with:
```javascript
        cachedOverviewData = data;
        if (sectorView === 'etfs') renderSectors(data.sectors);
        else renderThemes(data.themes);
        renderVix(data.vix);
        renderGex(data.gex);
        renderBreadth(data.breadth);
```

- [ ] **Step 2: Rebuild the Docker image and restart the API**

```bash
docker compose up --build -d api
```

Wait ~20 seconds for the build to complete.

- [ ] **Step 3: Verify Sector ETFs view via Playwright**

Use `mcp__playwright__browser_resize` to set viewport to 390×844 (iPhone 12), then navigate to `https://dev-mi.austin10berge.com` and switch to the Overview tab.

Confirm:
- Sectors card is full width (not in 2-column grid)
- Pill toggle shows "Sector ETFs | Themes" with "Sector ETFs" active (white background)
- Three columns visible: 1D, 1W, 1M with bars and percentages
- VIX, GEX, Breadth appear below in 2-column grid

- [ ] **Step 4: Verify Themes view via Playwright**

Click the "Themes" pill button. Confirm:
- List shows combined singles (Semis/Memory/SMH, Nuclear/NLR, etc.) and baskets (Hyperscalers, Cybersecurity, Crypto Proxy)
- Rows sorted by 1D descending
- Singles show ETF ticker in small muted text under the label
- Basket rows show ▶ chevron
- Clicking a basket row reveals constituent stocks and rotates chevron to ▼
- Clicking again collapses them back

- [ ] **Step 5: Verify toggle state persists across tab switches**

Switch to Watchlist tab, then back to Overview. Confirm the previously selected view (Sectors or Themes) resets to default "Sector ETFs" (ephemeral — expected behavior per spec).
