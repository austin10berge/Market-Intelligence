function openTradingView(symbol) {
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
    if (!isIOS) {
        window.open(`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`, '_blank');
        return;
    }
    const appUrl = `tradingview://chart?symbol=${encodeURIComponent(symbol)}`;
    const webUrl = `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`;
    let fallbackTimer = setTimeout(() => window.open(webUrl, '_blank'), 600);
    document.addEventListener('visibilitychange', () => clearTimeout(fallbackTimer), { once: true });
    window.location.href = appUrl;
}

const API_BASE = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || (() => {
    console.error('[Market Intelligence] FATAL: window.MARKET_INTELLIGENCE_CONFIG is not defined.');
    return '/MISSING_CONFIG_JS_SEE_CONSOLE';
})();

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
        case 'overview':   renderOverviewView();  break;
        case 'watchlist':  renderWatchlistView(); break;
        case 'scanner':    renderHandoffView('Scanner',    '/scanner.html',  'Universe and watchlist CSP scanning with custom parameters.'); break;
        case 'backtester': renderHandoffView('Backtester', '/backtest.html', 'Backtest CSP strategies against historical price data.');        break;
    }
}

// ── Watchlist state ───────────────────────────────────────────────────────────

let allStockCandidates = [];
let activeColGroup = 0;
let currentSort = { column: 'pct_1d', asc: false };
let lastStocksResponse = null;

let activeWatchlistTab = 'tickers';
const watchlistFetched = { tickers: false, csp: false, leaps: false };

// ── CSP state ─────────────────────────────────────────────────────────────────

let allCspCandidates = [];
let cspSort = { column: 'annualized_roc', asc: false };
let cspPage = 1;
const CSP_PER_PAGE = 10;
let lastCspResponse = null;

// ── LEAPS state ───────────────────────────────────────────────────────────────

let allLeapsCandidates = [];
let leapsSort = { column: 'premium_markup_percent', asc: true };
let leapsPage = 1;
const LEAPS_PER_PAGE = 10;
let lastLeapsResponse = null;

// ── Overview state ────────────────────────────────────────────────────────────

let cachedPostureData = null;
let cachedOverviewData = null;
let overviewFetched = false;

// ── Column group definitions ──────────────────────────────────────────────────

const COL_GROUPS = [
    {
        label: 'Price',
        cols: [
            { h: 'Price', key: 'price',   fmt: c => c.price > 0 ? `$${c.price.toFixed(2)}` : '—', pill: false, cls: () => 'primary' },
            { h: '1D %',  key: 'pct_1d',  fmt: c => fmtPct(c.pct_1d),                              pill: true,  cls: c => pctCls(c.pct_1d) },
            { h: '1W %',  key: 'pct_1w',  fmt: c => fmtPct(c.pct_1w),                              pill: true,  cls: c => pctCls(c.pct_1w) },
        ],
    },
    {
        label: 'Returns',
        cols: [
            { h: '1D %',  key: 'pct_1d',  fmt: c => fmtPct(c.pct_1d),  pill: true,  cls: c => pctCls(c.pct_1d) },
            { h: '1W %',  key: 'pct_1w',  fmt: c => fmtPct(c.pct_1w),  pill: true,  cls: c => pctCls(c.pct_1w) },
            { h: '1M %',  key: 'pct_1m',  fmt: c => fmtPct(c.pct_1m),  pill: true,  cls: c => pctCls(c.pct_1m) },
        ],
    },
    {
        label: 'Fundmtls',
        cols: [
            { h: 'P/E',   key: 'pe',          fmt: c => fmtNum(c.pe),          pill: false, cls: () => '' },
            { h: 'Fwd P/E', key: 'forward_pe', fmt: c => fmtNum(c.forward_pe), pill: false, cls: () => '' },
            { h: 'PEG',   key: 'peg_ratio',    fmt: c => fmtNum(c.peg_ratio),  pill: false, cls: () => '' },
        ],
    },
    {
        label: 'Options',
        cols: [
            { h: 'IV/RV', key: 'atm_iv_rv20',  fmt: c => fmtNum(c.atm_iv_rv20, 2),  pill: false, cls: () => '' },
            { h: 'IV%',   key: 'iv_percentile', fmt: c => fmtIvPct(c.iv_percentile), pill: false, cls: () => '' },
            { h: 'Beta',  key: 'beta',           fmt: c => fmtNum(c.beta),            pill: false, cls: () => '' },
        ],
    },
];

function fmtPct(v) {
    if (v == null || v === 'N/A') return '—';
    return `${v >= 0 ? '+' : ''}${parseFloat(v).toFixed(2)}%`;
}

function pctCls(v) {
    if (v == null || v === 'N/A') return 'neutral';
    const n = parseFloat(v);
    return n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral';
}

function fmtNum(v, dec = 2) {
    if (v == null || v === 'N/A') return '—';
    const n = parseFloat(v);
    return isNaN(n) ? '—' : n.toFixed(dec);
}

function fmtIvPct(v) {
    if (v == null || v === 'N/A') return '—';
    const n = parseFloat(v);
    return isNaN(n) ? '—' : `${n.toFixed(1)}%`;
}

function escHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function buildSparklineSVG(prices, isUp) {
    if (!prices || prices.length < 2) return '<span class="sparkline-empty">—</span>';

    const W = 100, H = 22, pad = 1;
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;
    const uid = `sg${Math.random().toString(36).slice(2, 6)}`;

    const pts = prices.map((p, i) => ({
        x: +(pad + (i / (prices.length - 1)) * (W - pad * 2)).toFixed(1),
        y: +(pad + (1 - (p - min) / range) * (H - pad * 2)).toFixed(1),
    }));

    const linePoints = pts.map(p => `${p.x},${p.y}`).join(' ');
    const first = pts[0], last = pts[pts.length - 1];
    const areaPath = `M${first.x},${first.y} ` +
        pts.slice(1).map(p => `L${p.x},${p.y}`).join(' ') +
        ` L${last.x},${H - pad} L${first.x},${H - pad} Z`;

    const color = isUp ? '#089981' : '#F23645';

    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <defs>
            <linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%"   stop-color="${color}" stop-opacity="0.28"/>
                <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
            </linearGradient>
        </defs>
        <path d="${areaPath}" fill="url(#${uid})"/>
        <polyline fill="none" stroke="${color}" stroke-width="1.5"
            stroke-linecap="round" stroke-linejoin="round" points="${linePoints}"/>
        <circle cx="${last.x}" cy="${last.y}" r="1.8" fill="${color}"/>
    </svg>`;
}

// ── Column group UI ───────────────────────────────────────────────────────────

function renderTabs() {
    document.getElementById('col-tabs').innerHTML = COL_GROUPS.map((g, i) =>
        `<button class="col-tab${i === activeColGroup ? ' active' : ''}" onclick="switchColGroup(${i})">${g.label}</button>`
    ).join('');
}

function renderColHeaders() {
    const group = COL_GROUPS[activeColGroup];
    const hdr = document.getElementById('col-header-row');
    hdr.innerHTML = `
        <div></div><div></div>
        ${group.cols.map(col => {
            const sorted = currentSort.column === col.key;
            return `<div class="ch-col${sorted ? ' sorted' : ''}" onclick="sortByCol('${col.key}')">
                ${col.h}${sorted ? ` <span class="sort-dir">${currentSort.asc ? '↑' : '↓'}</span>` : ''}
            </div>`;
        }).join('')}
    `;
}

function switchColGroup(idx) {
    const next = Math.max(0, Math.min(COL_GROUPS.length - 1, idx));
    if (next === activeColGroup) return;
    activeColGroup = next;
    currentSort = { column: COL_GROUPS[activeColGroup].cols[0].key, asc: false };
    renderTabs();
    renderColHeaders();
    renderStockCandidates(sortedCandidates());
}

function sortByCol(key) {
    currentSort.asc = currentSort.column === key ? !currentSort.asc : false;
    currentSort.column = key;
    renderColHeaders();
    renderStockCandidates(sortedCandidates());
}

function sortedCandidates() {
    return [...allStockCandidates].sort((a, b) => {
        let vA = a[currentSort.column], vB = b[currentSort.column];
        if (vA === 'N/A' || vA == null) return 1;
        if (vB === 'N/A' || vB == null) return -1;
        const nA = parseFloat(vA), nB = parseFloat(vB);
        if (!isNaN(nA) && !isNaN(nB)) return currentSort.asc ? nA - nB : nB - nA;
        return currentSort.asc
            ? String(vA).localeCompare(String(vB))
            : String(vB).localeCompare(String(vA));
    });
}

// ── Render ticker rows ────────────────────────────────────────────────────────

function renderStockCandidates(candidates) {
    const el = document.getElementById('stocks-list');
    if (!candidates || candidates.length === 0) {
        el.innerHTML = '<div class="list-message">No stocks found</div>';
        return;
    }

    const group = COL_GROUPS[activeColGroup];

    el.innerHTML = candidates.map((c, i) => {
        const rowDir = c.pct_1d > 0 ? 'up' : c.pct_1d < 0 ? 'down' : '';
        const sym = escHtml(c.symbol);

        const colCells = group.cols.map(col => {
            const cls = col.cls(c);
            const val = col.fmt(c);
            if (col.pill) {
                return `<div class="tr-col-wrap"><span class="tr-col pct-pill ${cls}">${val}</span></div>`;
            }
            return `<div class="tr-col ${cls}">${val}</div>`;
        }).join('');

        return `<div class="ticker-row ${rowDir}" style="--row-delay:${i * 25}ms" onclick="openTradingView('${sym}')">
            <div class="tr-left">
                <span class="tr-symbol">${sym}</span>
                <span class="tr-name">${escHtml(c.name)}</span>
            </div>
            <div class="tr-spark">${buildSparklineSVG(c.price_history_1m, c.pct_1m >= 0)}</div>
            ${colCells}
        </div>`;
    }).join('');
}

// ── Watchlist view ────────────────────────────────────────────────────────────

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

// ── Tickers content ───────────────────────────────────────────────────────────

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
        const badge = document.getElementById('cache-status-active');
        if (badge && lastStocksResponse) updateCacheStatusEl(badge, lastStocksResponse);
    }
}

// ── CSP content ───────────────────────────────────────────────────────────────

function renderCspContent() {
    document.getElementById('watchlist-content').innerHTML = `
        <div class="col-header-row" style="grid-template-columns:1fr auto auto auto; padding: 4px 14px 6px">
            <div></div>
            <div class="ch-col${cspSort.column === 'roc_percent' ? ' sorted' : ''}" onclick="sortCspBy('roc_percent')">ROC${cspSort.column === 'roc_percent' ? ` <span class="sort-dir">${cspSort.asc ? '↑' : '↓'}</span>` : ''}</div>
            <div class="ch-col${cspSort.column === 'annualized_roc' ? ' sorted' : ''}" onclick="sortCspBy('annualized_roc')">Yield${cspSort.column === 'annualized_roc' ? ` <span class="sort-dir">${cspSort.asc ? '↑' : '↓'}</span>` : ''}</div>
            <div class="ch-col${cspSort.column === 'dte' ? ' sorted' : ''}" onclick="sortCspBy('dte')">DTE${cspSort.column === 'dte' ? ` <span class="sort-dir">${cspSort.asc ? '↑' : '↓'}</span>` : ''}</div>
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

function sortCspBy(col) {
    cspSort.asc = cspSort.column === col ? !cspSort.asc : false;
    cspSort.column = col;
    cspPage = 1;
    renderCspContent();
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

// ── LEAPS content ─────────────────────────────────────────────────────────────

function renderLeapsContent() {
    document.getElementById('watchlist-content').innerHTML = `
        <div class="col-header-row" style="grid-template-columns:1fr auto auto auto; padding: 4px 14px 6px">
            <div></div>
            <div class="ch-col${leapsSort.column === 'premium_markup_percent' ? ' sorted' : ''}" onclick="sortLeapsBy('premium_markup_percent')">Markup${leapsSort.column === 'premium_markup_percent' ? ` <span class="sort-dir">${leapsSort.asc ? '↑' : '↓'}</span>` : ''}</div>
            <div class="ch-col${leapsSort.column === 'premium' ? ' sorted' : ''}" onclick="sortLeapsBy('premium')">Premium${leapsSort.column === 'premium' ? ` <span class="sort-dir">${leapsSort.asc ? '↑' : '↓'}</span>` : ''}</div>
            <div class="ch-col${leapsSort.column === 'expiration' ? ' sorted' : ''}" onclick="sortLeapsBy('expiration')">Expiry${leapsSort.column === 'expiration' ? ` <span class="sort-dir">${leapsSort.asc ? '↑' : '↓'}</span>` : ''}</div>
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

// ── Market Overview view ──────────────────────────────────────────────────────

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

// ── Handoff view ──────────────────────────────────────────────────────────────

function renderHandoffView(label, url, desc) {
    document.getElementById('main-content').innerHTML = `
        <div class="handoff-view">
            <div class="handoff-title">${label}</div>
            <p class="handoff-desc">${desc}</p>
            <a href="${url}" class="handoff-btn">Open ${label} →</a>
        </div>`;
}

// ── Cache status helpers ──────────────────────────────────────────────────────

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

// ── API ───────────────────────────────────────────────────────────────────────

async function fetchMarketPosture() {
    try {
        const res = await fetch(`${API_BASE}/market-posture`);
        if (!res.ok) throw new Error('Failed');
        const data = await res.json();
        cachedPostureData = data;
        renderPosture(data);
        if (activeTab === 'overview') renderOverviewPostureSection(data);
    } catch {
        const el = document.getElementById('posture-widget');
        el.className = 'posture-pill neutral';
        el.innerHTML = '<span class="pulse-dot"></span>Unavailable';
    }
}

function renderPosture(data) {
    const el = document.getElementById('posture-widget');
    const scoreEl = document.getElementById('composite-score');
    const txt = data.posture || 'Neutral';
    el.className = `posture-pill ${txt.includes('Bullish') ? 'bullish' : txt.includes('Bearish') ? 'bearish' : 'neutral'}`;
    el.innerHTML = `<span class="pulse-dot"></span>${txt}`;
    if (scoreEl && data.composite_score != null) {
        const s = parseFloat(data.composite_score).toFixed(3);
        scoreEl.textContent = `${s > 0 ? '+' : ''}${s}`;
    }
}

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
    el.innerHTML = `
        <div class="breadth-row">
            <span class="breadth-label">200d MA</span>
            <div class="breadth-bar-track"><div class="breadth-bar-fill ${maColor}" style="width:${pct.toFixed(1)}%"></div></div>
            <span class="breadth-value ${maColor}">${pct.toFixed(0)}%</span>
        </div>
        <div class="breadth-ad">A/D &nbsp; ${breadth.advancing}↑ / ${breadth.declining}↓ &nbsp; ratio ${breadth.ad_ratio != null ? breadth.ad_ratio.toFixed(2) : '—'}</div>`;
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    renderBottomNav();
    fetchMarketPosture();
    switchTab('watchlist');
});
