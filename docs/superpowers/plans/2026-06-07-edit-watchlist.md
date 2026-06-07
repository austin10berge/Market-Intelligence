# v2 Edit Watchlist — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native v2 SPA edit-watchlist surface (Stock/CSP tickers, YouTube channels, CSP settings) accessed via a pencil icon on the Watchlist tab header.

**Architecture:** All changes confined to two files — `src/web/v2/index.html` (CSS additions) and `src/web/v2/app.js` (JS additions). A pencil button on the Watchlist header calls `renderEditWatchlistView()`, replacing `#main-content` with a sub-view that has three sub-tabs (Watchlists, Channels, Settings). A shared `initChipEditor()` factory manages chip-tag editing state for both watchlist sections and the YouTube channels section. The Settings tab reuses existing `.scn-field` / `.scn-field-input` classes with a live weight-sum badge.

**Tech Stack:** Vanilla JS, existing CSS variables (`--tv-*`), IBM Plex Mono/Sans Condensed, Playwright MCP for browser verification.

---

## File Map

| File | Change |
|---|---|
| `src/web/v2/index.html` | Add ~90 lines of CSS in the `<style>` block (before `</style>`) |
| `src/web/v2/app.js` | Add ~10 lines of state, modify `renderWatchlistView()`, add ~240 lines of new functions |

No new files. No backend changes.

---

## Task 1: Add CSS for edit view

**Files:**
- Modify: `src/web/v2/index.html` — inside `<style>`, just before `</style>` (line ~1020)

- [ ] **Step 1: Add all new CSS classes**

Insert the following block immediately before the closing `</style>` tag in `src/web/v2/index.html`:

```css
        /* ── Edit Watchlist ── */
        .edit-pencil-btn {
            display: flex;
            align-items: center;
            padding: 4px 6px;
            border: none;
            background: none;
            color: var(--tv-muted);
            cursor: pointer;
            transition: color 0.15s, background 0.15s;
            border-radius: 4px;
            line-height: 1;
            -webkit-tap-highlight-color: transparent;
        }
        .edit-pencil-btn:hover { color: var(--tv-text); background: rgba(255,255,255,0.05); }

        .edit-back-btn {
            display: flex;
            align-items: center;
            gap: 5px;
            padding: 0;
            border: none;
            background: none;
            color: var(--tv-muted);
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.04em;
            cursor: pointer;
            transition: color 0.15s;
            -webkit-tap-highlight-color: transparent;
        }
        .edit-back-btn:hover { color: var(--tv-text); }

        .edit-chip-editor {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            align-items: center;
            padding: 8px 0 10px;
            min-height: 40px;
        }

        .edit-chip {
            display: inline-flex;
            align-items: center;
            gap: 3px;
            padding: 3px 4px 3px 8px;
            border-radius: 99px;
            background: rgba(41,98,255,0.1);
            border: 1px solid rgba(41,98,255,0.3);
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            font-weight: 600;
            color: #5B8AF5;
            white-space: nowrap;
            letter-spacing: 0.03em;
        }

        .edit-chip-remove {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 16px;
            height: 16px;
            padding: 0;
            border: none;
            background: none;
            color: var(--tv-muted);
            font-size: 14px;
            line-height: 1;
            cursor: pointer;
            border-radius: 50%;
            transition: color 0.12s, background 0.12s;
            flex-shrink: 0;
        }
        .edit-chip-remove:hover { color: var(--tv-text); background: rgba(255,255,255,0.1); }

        .edit-chip-input {
            border: none;
            background: none;
            outline: none;
            color: var(--tv-text);
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            font-weight: 600;
            letter-spacing: 0.03em;
            min-width: 80px;
            width: 80px;
            text-transform: uppercase;
            caret-color: var(--tv-blue);
        }
        .edit-chip-input::placeholder {
            color: var(--tv-muted);
            opacity: 0.5;
            text-transform: none;
            letter-spacing: 0;
        }

        .edit-weight-sum-badge {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            padding: 2px 8px;
            border-radius: 99px;
            border: 1px solid transparent;
            display: inline-block;
            margin-top: 10px;
            transition: color 0.2s, background 0.2s, border-color 0.2s;
        }
        .edit-weight-sum-badge.valid   { color: var(--tv-green);  background: rgba(8,153,129,0.1);   border-color: rgba(8,153,129,0.3);  }
        .edit-weight-sum-badge.close   { color: var(--tv-yellow); background: rgba(247,201,72,0.1);  border-color: rgba(247,201,72,0.3); }
        .edit-weight-sum-badge.invalid { color: var(--tv-red);    background: rgba(242,54,69,0.1);   border-color: rgba(242,54,69,0.3);  }

        .edit-toggle-wrapper { position: relative; display: inline-block; flex-shrink: 0; }
        .edit-toggle-input   { position: absolute; opacity: 0; width: 0; height: 0; }
        .edit-toggle-track {
            display: block;
            width: 40px; height: 22px;
            background: var(--tv-surface2);
            border: 1px solid var(--tv-border);
            border-radius: 9999px;
            cursor: pointer;
            transition: background 0.2s, border-color 0.2s;
            position: relative;
        }
        .edit-toggle-track::after {
            content: '';
            position: absolute;
            top: 3px; left: 3px;
            width: 14px; height: 14px;
            background: var(--tv-muted);
            border-radius: 50%;
            transition: transform 0.2s, background 0.2s;
        }
        .edit-toggle-input:checked + .edit-toggle-track {
            background: rgba(41,98,255,0.25);
            border-color: rgba(41,98,255,0.5);
        }
        .edit-toggle-input:checked + .edit-toggle-track::after {
            transform: translateX(18px);
            background: var(--tv-blue);
        }

        .edit-status {
            font-family: 'IBM Plex Mono', monospace;
            font-size: 12px;
            font-weight: 500;
            min-height: 18px;
            margin-top: 6px;
        }
        .edit-status.success { color: var(--tv-green); }
        .edit-status.error   { color: var(--tv-red); }

        @keyframes shake {
            0%,100% { transform: translateX(0); }
            20%      { transform: translateX(-4px); }
            40%      { transform: translateX(4px); }
            60%      { transform: translateX(-3px); }
            80%      { transform: translateX(3px); }
        }
        .shake { animation: shake 0.4s ease; }
```

- [ ] **Step 2: Verify CSS renders without errors**

Open the browser and check the dev console for any CSS parse errors:

```
Playwright: browser_navigate to https://dev-mi.austin10berge.com/v2/
browser_snapshot — confirm page loads, no red console errors
```

- [ ] **Step 3: Commit**

```bash
git add src/web/v2/index.html
git commit -m "feat(v2): add CSS for edit-watchlist chip editor, toggle, weight badge"
```

---

## Task 2: Entry point + navigation skeleton

**Files:**
- Modify: `src/web/v2/app.js`
  - Add state variables after line 89 (end of existing state block)
  - Replace `renderWatchlistView()` (lines 291–299)
  - Add four new functions after the existing `renderLeapsContent` block (~line 517)

- [ ] **Step 1: Add edit state variables**

In `app.js`, after this existing block (line ~89):
```javascript
let overviewFetched = false;
```

Add:
```javascript
// ── Edit Watchlist state ──────────────────────────────────────────────────────

let activeEditTab = 'watchlists';
let stockChipEditor = null;
let cspWlChipEditor = null;
let channelsChipEditor = null;
```

- [ ] **Step 2: Replace `renderWatchlistView()` to add the pencil button**

Replace the existing `renderWatchlistView` function (lines 291–299):
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
```

With:
```javascript
function renderWatchlistView() {
    document.getElementById('main-content').innerHTML = `
        <div class="section-header" style="padding-bottom:8px">
            <div class="watchlist-sub-tabs" id="watchlist-sub-tabs"></div>
            <div style="display:flex;align-items:center;gap:6px">
                <span class="cache-badge" id="cache-status-active"></span>
                <button class="edit-pencil-btn" onclick="renderEditWatchlistView()" title="Edit watchlists">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="m18.5 2.5 3 3L12 15H9v-3L18.5 2.5z"/></svg>
                </button>
            </div>
        </div>
        <div id="watchlist-content"></div>`;
    renderWatchlistSubTabs();
    showWatchlistTab(activeWatchlistTab);
}
```

- [ ] **Step 3: Add navigation functions**

Add these four functions anywhere after `renderLeapsContent` (around line 517) and before `renderOverviewView`:

```javascript
// ── Edit Watchlist view ───────────────────────────────────────────────────────

function renderEditWatchlistView() {
    activeEditTab = 'watchlists';
    stockChipEditor = null;
    cspWlChipEditor = null;
    channelsChipEditor = null;
    document.getElementById('main-content').innerHTML = `
        <div class="section-header" style="padding-bottom:8px">
            <button class="edit-back-btn" onclick="backToWatchlist()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" width="14" height="14"><polyline points="15 18 9 12 15 6"/></svg>
                WATCHLIST
            </button>
            <span class="section-title">Edit Watchlist</span>
        </div>
        <div class="watchlist-sub-tabs" id="edit-sub-tabs" style="padding: 0 16px 8px"></div>
        <div id="edit-content"></div>`;
    renderEditSubTabs();
    switchEditTab('watchlists');
}

function renderEditSubTabs() {
    const tabs = [
        { id: 'watchlists', label: 'Watchlists' },
        { id: 'channels',   label: 'Channels' },
        { id: 'settings',   label: 'Settings' },
    ];
    document.getElementById('edit-sub-tabs').innerHTML = tabs.map(t =>
        `<button class="sub-tab${t.id === activeEditTab ? ' active' : ''}" onclick="switchEditTab('${t.id}')">${t.label}</button>`
    ).join('');
}

function switchEditTab(tab) {
    activeEditTab = tab;
    renderEditSubTabs();
    switch (tab) {
        case 'watchlists': renderEditWatchlistsTab(); break;
        case 'channels':   renderEditChannelsTab();   break;
        case 'settings':   renderEditSettingsTab();   break;
    }
}

function backToWatchlist() {
    renderWatchlistView();
}
```

- [ ] **Step 4: Verify navigation in browser**

```
Playwright: browser_navigate to https://dev-mi.austin10berge.com/v2/
browser_snapshot — confirm pencil icon appears in Watchlist header
browser_click the pencil button
browser_snapshot — confirm edit view renders with ← WATCHLIST back button, "EDIT" title, and three sub-tabs (Watchlists, Channels, Settings)
browser_click the ← WATCHLIST button
browser_snapshot — confirm returns to Watchlist view with Tickers/CSP/LEAPS sub-tabs
```

- [ ] **Step 5: Commit**

```bash
git add src/web/v2/app.js
git commit -m "feat(v2): add edit-watchlist entry point, navigation, and sub-tab skeleton"
```

---

## Task 3: Shared chip editor + status helper

**Files:**
- Modify: `src/web/v2/app.js` — add after the new navigation functions from Task 2

- [ ] **Step 1: Add `showEditStatus` helper**

Add immediately after the `backToWatchlist` function:

```javascript
function showEditStatus(el, msg, cls) {
    if (!el) return;
    el.textContent = msg;
    el.className = `edit-status ${cls}`;
    setTimeout(() => { if (el.textContent === msg) { el.textContent = ''; el.className = 'edit-status'; } }, 4000);
}
```

- [ ] **Step 2: Add `initChipEditor` factory**

Add immediately after `showEditStatus`:

```javascript
function initChipEditor(containerEl, initialValues, opts = {}) {
    // opts.urlMode: bool — validates youtube.com/ and shows @handle as label
    // opts.placeholder: string — input placeholder text
    let values = [...initialValues];
    const chipsEl = containerEl.querySelector('.edit-chips');
    const inputEl = containerEl.querySelector('.edit-chip-input');

    function renderChips() {
        if (!chipsEl) return;
        chipsEl.innerHTML = values.map((v, i) => {
            const label = opts.urlMode ? extractYouTubeHandle(v) : v;
            return `<span class="edit-chip">
                <span class="edit-chip-label">${escHtml(label)}</span>
                <button class="edit-chip-remove" data-idx="${i}" type="button">×</button>
            </span>`;
        }).join('');
        chipsEl.querySelectorAll('.edit-chip-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                values.splice(parseInt(btn.dataset.idx), 1);
                renderChips();
            });
        });
    }

    if (inputEl) {
        inputEl.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault();
                const raw = inputEl.value.replace(/,/g, '').trim();
                if (!raw) return;
                const val = opts.urlMode ? raw : raw.toUpperCase();
                if (opts.urlMode && !val.includes('youtube.com/')) {
                    inputEl.classList.add('shake');
                    setTimeout(() => inputEl.classList.remove('shake'), 500);
                    return;
                }
                if (!values.includes(val)) values.push(val);
                inputEl.value = '';
                renderChips();
            } else if (e.key === 'Escape') {
                inputEl.value = '';
            }
        });
    }

    renderChips();
    return { getValues: () => [...values] };
}
```

- [ ] **Step 3: Add `extractYouTubeHandle` helper**

Add immediately after `initChipEditor`:

```javascript
function extractYouTubeHandle(url) {
    try {
        const match = url.match(/youtube\.com\/@?([\w.-]+)/);
        if (match) return `@${match[1]}`;
        const u = new URL(url);
        const part = u.pathname.replace(/^\//, '').split('/')[0];
        return part || url;
    } catch {
        return url;
    }
}
```

- [ ] **Step 4: Commit**

```bash
git add src/web/v2/app.js
git commit -m "feat(v2): add initChipEditor factory, showEditStatus, extractYouTubeHandle helpers"
```

---

## Task 4: Watchlists tab

**Files:**
- Modify: `src/web/v2/app.js` — add after `extractYouTubeHandle`

- [ ] **Step 1: Add `renderEditWatchlistsTab`**

```javascript
function renderEditWatchlistsTab() {
    document.getElementById('edit-content').innerHTML = `
        <div style="padding: 8px 14px 0">
            <div class="overview-card" style="margin-bottom:10px">
                <div class="overview-card-title">Stock Watchlist</div>
                <div id="stock-chips-wrap">
                    <div class="list-message loading" style="padding:12px 0;font-size:14px">Loading…</div>
                </div>
                <button class="scn-sheet-apply" id="save-stock-btn" onclick="saveStockWatchlistEdit()"
                    style="width:100%;margin-top:10px">Save</button>
                <div class="edit-status" id="stock-edit-status"></div>
            </div>
            <div class="overview-card">
                <div class="overview-card-title">CSP Watchlist</div>
                <div id="csp-wl-chips-wrap">
                    <div class="list-message loading" style="padding:12px 0;font-size:14px">Loading…</div>
                </div>
                <button class="scn-sheet-apply" id="save-csp-wl-btn" onclick="saveCspWatchlistEdit()"
                    style="width:100%;margin-top:10px">Save</button>
                <div class="edit-status" id="csp-wl-edit-status"></div>
            </div>
        </div>`;

    Promise.all([
        fetch(`${API_BASE}/watchlist/stock`).then(r => r.json()),
        fetch(`${API_BASE}/watchlist`).then(r => r.json()),
    ]).then(([stockData, cspData]) => {
        const stockWrap = document.getElementById('stock-chips-wrap');
        const cspWrap   = document.getElementById('csp-wl-chips-wrap');
        if (stockWrap) {
            stockWrap.innerHTML = `<div class="edit-chip-editor"><div class="edit-chips"></div><input class="edit-chip-input" placeholder="+ ADD TICKER" /></div>`;
            stockChipEditor = initChipEditor(stockWrap, stockData.watchlist || []);
        }
        if (cspWrap) {
            cspWrap.innerHTML = `<div class="edit-chip-editor"><div class="edit-chips"></div><input class="edit-chip-input" placeholder="+ ADD TICKER" /></div>`;
            cspWlChipEditor = initChipEditor(cspWrap, cspData.watchlist || []);
        }
    }).catch(() => {
        const stockWrap = document.getElementById('stock-chips-wrap');
        const cspWrap   = document.getElementById('csp-wl-chips-wrap');
        if (stockWrap) stockWrap.innerHTML = '<div class="edit-status error" style="padding:8px 0">Failed to load</div>';
        if (cspWrap)   cspWrap.innerHTML   = '<div class="edit-status error" style="padding:8px 0">Failed to load</div>';
    });
}
```

- [ ] **Step 2: Add `saveStockWatchlistEdit` and `saveCspWatchlistEdit`**

```javascript
async function saveStockWatchlistEdit() {
    if (!stockChipEditor) return;
    const btn = document.getElementById('save-stock-btn');
    const statusEl = document.getElementById('stock-edit-status');
    btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/watchlist/stock`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tickers: stockChipEditor.getValues() }),
        });
        if (!res.ok) throw new Error();
        showEditStatus(statusEl, '✓ Saved', 'success');
        watchlistFetched.tickers = false;
        allStockCandidates = [];
        lastStocksResponse = null;
    } catch {
        showEditStatus(statusEl, '✗ Failed', 'error');
    } finally {
        btn.disabled = false;
    }
}

async function saveCspWatchlistEdit() {
    if (!cspWlChipEditor) return;
    const btn = document.getElementById('save-csp-wl-btn');
    const statusEl = document.getElementById('csp-wl-edit-status');
    btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/watchlist`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tickers: cspWlChipEditor.getValues() }),
        });
        if (!res.ok) throw new Error();
        showEditStatus(statusEl, '✓ Saved — re-scan started', 'success');
        watchlistFetched.csp = false;
        watchlistFetched.leaps = false;
        allCspCandidates = [];
        allLeapsCandidates = [];
        lastCspResponse = null;
        lastLeapsResponse = null;
    } catch {
        showEditStatus(statusEl, '✗ Failed', 'error');
    } finally {
        btn.disabled = false;
    }
}
```

- [ ] **Step 3: Verify Watchlists tab in browser**

```
Playwright: browser_navigate to https://dev-mi.austin10berge.com/v2/
browser_click pencil button
browser_snapshot — confirm "Watchlists" sub-tab is active and both chip editors loaded with existing tickers
Add a ticker: browser_click the Stock Watchlist chip input, browser_type "TSLA", browser_press_key "Enter"
browser_snapshot — confirm TSLA chip appears in Stock Watchlist
Remove a chip: browser_click the × on any chip
browser_snapshot — confirm chip is removed
browser_click "Save" on Stock Watchlist
browser_snapshot — confirm "✓ Saved" status appears
```

- [ ] **Step 4: Commit**

```bash
git add src/web/v2/app.js
git commit -m "feat(v2): edit-watchlist — Watchlists tab with chip editors for stock and CSP"
```

---

## Task 5: Channels tab

**Files:**
- Modify: `src/web/v2/app.js` — add after `saveCspWatchlistEdit`

- [ ] **Step 1: Add `renderEditChannelsTab`**

```javascript
function renderEditChannelsTab() {
    document.getElementById('edit-content').innerHTML = `
        <div style="padding: 8px 14px 0">
            <div class="overview-card">
                <div class="overview-card-title">YouTube Channels</div>
                <div id="channels-chips-wrap">
                    <div class="list-message loading" style="padding:12px 0;font-size:14px">Loading…</div>
                </div>
                <button class="scn-sheet-apply" id="save-channels-btn" onclick="saveChannelsEdit()"
                    style="width:100%;margin-top:10px">Save</button>
                <div class="edit-status" id="channels-edit-status"></div>
            </div>
        </div>`;

    fetch(`${API_BASE}/youtube-channels`)
        .then(r => r.json())
        .then(data => {
            const wrap = document.getElementById('channels-chips-wrap');
            if (!wrap) return;
            wrap.innerHTML = `<div class="edit-chip-editor"><div class="edit-chips"></div><input class="edit-chip-input" placeholder="+ ADD URL" /></div>`;
            channelsChipEditor = initChipEditor(wrap, data.channels || [], { urlMode: true });
        })
        .catch(() => {
            const wrap = document.getElementById('channels-chips-wrap');
            if (wrap) wrap.innerHTML = '<div class="edit-status error" style="padding:8px 0">Failed to load</div>';
        });
}
```

- [ ] **Step 2: Add `saveChannelsEdit`**

```javascript
async function saveChannelsEdit() {
    if (!channelsChipEditor) return;
    const btn = document.getElementById('save-channels-btn');
    const statusEl = document.getElementById('channels-edit-status');
    btn.disabled = true;
    try {
        const res = await fetch(`${API_BASE}/youtube-channels`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ channels: channelsChipEditor.getValues() }),
        });
        if (!res.ok) throw new Error();
        showEditStatus(statusEl, '✓ Saved', 'success');
    } catch {
        showEditStatus(statusEl, '✗ Failed', 'error');
    } finally {
        btn.disabled = false;
    }
}
```

- [ ] **Step 3: Verify Channels tab in browser**

```
Playwright: browser_navigate to https://dev-mi.austin10berge.com/v2/
browser_click pencil button
browser_click "Channels" sub-tab
browser_snapshot — confirm YouTube channel chips load (showing @handle labels)
Try adding an invalid URL: browser_click channel input, browser_type "notaurl", browser_press_key "Enter"
browser_snapshot — confirm no chip added (shake animation fires but not visible in snapshot — check chip count is unchanged)
Try adding a valid URL: browser_type "https://www.youtube.com/@SomeChannel", browser_press_key "Enter"
browser_snapshot — confirm chip appears with label "@SomeChannel"
```

- [ ] **Step 4: Commit**

```bash
git add src/web/v2/app.js
git commit -m "feat(v2): edit-watchlist — Channels tab with YouTube URL chip editor"
```

---

## Task 6: Settings tab

**Files:**
- Modify: `src/web/v2/app.js` — add after `saveChannelsEdit`

- [ ] **Step 1: Add `renderEditSettingsTab`**

```javascript
function renderEditSettingsTab() {
    document.getElementById('edit-content').innerHTML = `
        <div style="padding: 8px 14px 0">
            <div class="overview-card" style="margin-bottom:10px">
                <div class="scn-sheet-section-title">Contract Filters</div>
                <div class="scn-field">
                    <span class="scn-field-label">Min DTE</span>
                    <input type="number" id="csp-min-dte" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Max DTE</span>
                    <input type="number" id="csp-max-dte" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Min Delta</span>
                    <input type="number" id="csp-min-delta" step="0.01" min="0" max="1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Max Delta</span>
                    <input type="number" id="csp-max-delta" step="0.01" min="0" max="1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Min Capital ROC %</span>
                    <input type="number" id="csp-min-roc" step="0.1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Max Spread %</span>
                    <input type="number" id="csp-max-spread" step="0.1" class="scn-field-input" />
                </div>
            </div>
            <div class="overview-card" style="margin-bottom:10px">
                <div class="scn-sheet-section-title">Technical Filters</div>
                <div class="scn-field">
                    <span class="scn-field-label">Min IV %</span>
                    <input type="number" id="csp-min-iv" step="0.5" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Min RSI</span>
                    <input type="number" id="csp-min-rsi" step="1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Max RSI</span>
                    <input type="number" id="csp-max-rsi" step="1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Min ADX</span>
                    <input type="number" id="csp-min-adx" step="1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Max ADX</span>
                    <input type="number" id="csp-max-adx" step="1" class="scn-field-input" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">Pullback Mode</span>
                    <label class="edit-toggle-wrapper">
                        <input type="checkbox" id="csp-pullback" class="edit-toggle-input" />
                        <span class="edit-toggle-track"></span>
                    </label>
                </div>
            </div>
            <div class="overview-card" style="margin-bottom:10px">
                <div class="scn-sheet-section-title">Score Weights</div>
                <div class="scn-field">
                    <span class="scn-field-label">Annualized Yield</span>
                    <input type="number" id="csp-w-ay" step="0.05" min="0" max="1" class="scn-field-input" oninput="updateWeightSum()" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">PoP Proxy</span>
                    <input type="number" id="csp-w-pop" step="0.05" min="0" max="1" class="scn-field-input" oninput="updateWeightSum()" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">IV Percentile</span>
                    <input type="number" id="csp-w-iv" step="0.05" min="0" max="1" class="scn-field-input" oninput="updateWeightSum()" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">RSI Quality</span>
                    <input type="number" id="csp-w-rsi" step="0.05" min="0" max="1" class="scn-field-input" oninput="updateWeightSum()" />
                </div>
                <div class="scn-field">
                    <span class="scn-field-label">ADX Trend</span>
                    <input type="number" id="csp-w-adx" step="0.05" min="0" max="1" class="scn-field-input" oninput="updateWeightSum()" />
                </div>
                <div id="weight-sum-badge" class="edit-weight-sum-badge invalid">Sum: —</div>
                <button class="scn-sheet-apply" id="save-settings-btn" onclick="saveCspSettingsEdit()"
                    style="width:100%;margin-top:10px" disabled>Save &amp; Re-scan</button>
                <div class="edit-status" id="settings-edit-status"></div>
            </div>
        </div>`;

    fetch(`${API_BASE}/settings/csp`)
        .then(r => r.json())
        .then(data => {
            const s = data.settings;
            document.getElementById('csp-min-dte').value    = s.min_dte         ?? 30;
            document.getElementById('csp-max-dte').value    = s.max_dte         ?? 45;
            document.getElementById('csp-min-delta').value  = s.min_delta       ?? 0.15;
            document.getElementById('csp-max-delta').value  = s.max_delta       ?? 0.40;
            document.getElementById('csp-min-roc').value    = s.min_roc         ?? 1.0;
            document.getElementById('csp-max-spread').value = s.max_spread_pct  ?? 25.0;
            document.getElementById('csp-min-iv').value     = s.min_iv          ?? 25.0;
            document.getElementById('csp-min-rsi').value    = s.min_rsi         ?? 38.0;
            document.getElementById('csp-max-rsi').value    = s.max_rsi         ?? 65.0;
            document.getElementById('csp-min-adx').value    = s.min_adx         ?? 15.0;
            document.getElementById('csp-max-adx').value    = s.max_adx         ?? 40.0;
            document.getElementById('csp-pullback').checked = s.pullback_mode   ?? false;
            document.getElementById('csp-w-ay').value       = s.score_weight_ay      ?? 0.35;
            document.getElementById('csp-w-pop').value      = s.score_weight_pop     ?? 0.20;
            document.getElementById('csp-w-iv').value       = s.score_weight_iv_pct  ?? 0.20;
            document.getElementById('csp-w-rsi').value      = s.score_weight_rsi     ?? 0.15;
            document.getElementById('csp-w-adx').value      = s.score_weight_adx     ?? 0.10;
            updateWeightSum();
        })
        .catch(() => {
            showEditStatus(document.getElementById('settings-edit-status'), '✗ Failed to load settings', 'error');
        });
}
```

- [ ] **Step 2: Add `updateWeightSum` and `saveCspSettingsEdit`**

```javascript
function updateWeightSum() {
    const ids = ['csp-w-ay', 'csp-w-pop', 'csp-w-iv', 'csp-w-rsi', 'csp-w-adx'];
    const sum = ids.reduce((acc, id) => acc + (parseFloat(document.getElementById(id)?.value) || 0), 0);
    const badge = document.getElementById('weight-sum-badge');
    const btn   = document.getElementById('save-settings-btn');
    if (!badge) return;

    const valid = Math.abs(sum - 1.0) <= 0.001;
    const close = !valid && sum >= 0.95 && sum <= 1.05;

    badge.textContent = `Sum: ${sum.toFixed(2)}`;
    badge.className   = `edit-weight-sum-badge ${valid ? 'valid' : close ? 'close' : 'invalid'}`;
    if (btn) btn.disabled = !valid;
}

async function saveCspSettingsEdit() {
    const btn = document.getElementById('save-settings-btn');
    const statusEl = document.getElementById('settings-edit-status');
    btn.disabled = true;
    try {
        const payload = {
            min_dte:             parseInt(document.getElementById('csp-min-dte').value),
            max_dte:             parseInt(document.getElementById('csp-max-dte').value),
            min_delta:           parseFloat(document.getElementById('csp-min-delta').value),
            max_delta:           parseFloat(document.getElementById('csp-max-delta').value),
            min_roc:             parseFloat(document.getElementById('csp-min-roc').value),
            max_spread_pct:      parseFloat(document.getElementById('csp-max-spread').value),
            min_iv:              parseFloat(document.getElementById('csp-min-iv').value),
            min_rsi:             parseFloat(document.getElementById('csp-min-rsi').value),
            max_rsi:             parseFloat(document.getElementById('csp-max-rsi').value),
            min_adx:             parseFloat(document.getElementById('csp-min-adx').value),
            max_adx:             parseFloat(document.getElementById('csp-max-adx').value),
            pullback_mode:       document.getElementById('csp-pullback').checked,
            score_weight_ay:     parseFloat(document.getElementById('csp-w-ay').value),
            score_weight_pop:    parseFloat(document.getElementById('csp-w-pop').value),
            score_weight_iv_pct: parseFloat(document.getElementById('csp-w-iv').value),
            score_weight_rsi:    parseFloat(document.getElementById('csp-w-rsi').value),
            score_weight_adx:    parseFloat(document.getElementById('csp-w-adx').value),
        };
        const res = await fetch(`${API_BASE}/settings/csp`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error();
        showEditStatus(statusEl, '✓ Saved — re-scan running (~30–60s)', 'success');
        watchlistFetched.csp = false;
        watchlistFetched.leaps = false;
    } catch {
        showEditStatus(statusEl, '✗ Failed to save', 'error');
    } finally {
        updateWeightSum();
    }
}
```

- [ ] **Step 3: Verify Settings tab in browser**

```
Playwright: browser_navigate to https://dev-mi.austin10berge.com/v2/
browser_click pencil button
browser_click "Settings" sub-tab
browser_snapshot — confirm all three sections render (Contract Filters, Technical Filters, Score Weights) with field values loaded from API
Confirm weight-sum badge shows green "Sum: 1.00" (if defaults sum to 1.0) and Save button is enabled
Change a weight to make sum != 1.0: browser_click csp-w-ay field, clear it, browser_type "0.99"
browser_snapshot — confirm badge turns red, Save button is disabled
Restore: browser_click csp-w-ay, clear, browser_type "0.35"
browser_snapshot — confirm badge returns to green, Save button re-enabled
```

- [ ] **Step 4: Commit**

```bash
git add src/web/v2/app.js
git commit -m "feat(v2): edit-watchlist — Settings tab with CSP screener params and weight validation"
```

---

## Task 7: End-to-end browser verification

Full golden-path test through all three tabs.

- [ ] **Step 1: Full flow smoke test**

```
Playwright: browser_navigate to https://dev-mi.austin10berge.com/v2/
browser_snapshot — Watchlist tab loads, pencil icon visible in header

# ── Watchlists tab ──
browser_click pencil icon
browser_snapshot — edit view: ← WATCHLIST, EDIT title, three sub-tabs, Watchlists active
# Add a ticker
browser_click Stock Watchlist chip input (edit-chip-input in stock-chips-wrap)
browser_type "NVDA"
browser_press_key "Enter"
browser_snapshot — NVDA chip appears in Stock Watchlist
browser_click "Save" (save-stock-btn)
browser_wait_for text "✓ Saved"
browser_snapshot — success status visible

# ── Channels tab ──
browser_click "Channels" sub-tab
browser_snapshot — YouTube channel chips load
# Try invalid URL
browser_click channels chip input
browser_type "badurl"
browser_press_key "Enter"
browser_snapshot — no new chip, input still present

# ── Settings tab ──
browser_click "Settings" sub-tab
browser_snapshot — all fields populated, weight-sum badge green

# ── Back navigation ──
browser_click "← WATCHLIST" back button
browser_snapshot — Watchlist view restored with Tickers/CSP/LEAPS sub-tabs and pencil icon still present
```

- [ ] **Step 2: Verify cache invalidation on save**

```
Playwright: navigate to Watchlist → Tickers, note current ticker list
Navigate to pencil → Watchlists tab
Add a ticker, Save Stock Watchlist
browser_click ← WATCHLIST
browser_snapshot — Tickers tab refreshes with updated list (watchlistFetched.tickers was reset to false)
```

- [ ] **Step 3: Final commit**

```bash
git add src/web/v2/app.js src/web/v2/index.html
git commit -m "feat(v2): edit-watchlist — full end-to-end verified"
```
