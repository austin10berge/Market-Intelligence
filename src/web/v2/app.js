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
    analysis:   `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
    wheel:      `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>`,
};

const NAV_LABELS = {
    overview: 'Overview', watchlist: 'Watchlist',
    scanner: 'Scanner', backtester: 'Backtest', analysis: 'Charts', wheel: 'Wheel',
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
    if (activeTab === 'scanner'    && tab !== 'scanner'    && window.ScannerView)    window.ScannerView.teardown();
    if (activeTab === 'backtester' && tab !== 'backtester' && window.BacktesterView) window.BacktesterView.teardown();
    if (activeTab === 'analysis'   && tab !== 'analysis'   && window.AnalysisView)   window.AnalysisView.teardown();
    if (activeTab === 'wheel'      && tab !== 'wheel'      && window.WheelView)      window.WheelView.teardown();
    activeTab = tab;
    renderBottomNav();
    const mainContent = document.getElementById('main-content');
    switch (tab) {
        case 'overview':   renderOverviewView();  break;
        case 'watchlist':  renderWatchlistView(); break;
        case 'scanner':    window.ScannerView.render(mainContent); break;
        case 'backtester': window.BacktesterView.render(mainContent); break;
        case 'analysis':   window.AnalysisView.render(mainContent); break;
        case 'wheel':      window.WheelView.render(mainContent); break;
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

let cachedPostureData  = null;
let cachedOverviewData = null;
let overviewFetched    = false;
let sectorView         = 'etfs';
let sectorTf           = '1d';

// ── Edit Watchlist state ──────────────────────────────────────────────────────

let activeEditTab = 'watchlists';
let stockChipEditor = null;
let cspWlChipEditor = null;
let channelsChipEditor = null;

// ── Column group definitions ──────────────────────────────────────────────────

const COL_GROUPS = [
    {
        label: 'Price',
        cols: [
            { h: 'Price', key: 'price',  fmt: c => c.price > 0 ? `$${c.price.toFixed(2)}` : '—', pill: false, cls: c => `primary ${pctCls(c.pct_1d)}` },
            { h: '1D %',  key: 'pct_1d', fmt: c => fmtPct(c.pct_1d),                              pill: true,  cls: c => pctCls(c.pct_1d) },
            { h: 'TA',    render: c => `<div class="tr-ta-col">${buildTaAnnotations(c)}</div>` },
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

// Shared with the v2 scanner's Stock Performance table (src/web/v2/scanner.js) so the
// column definitions, formatting, and sparkline stay single-source. COL_GROUPS' col
// fmt/cls closures carry their own helpers, so consumers only need these two.
window.MITickers = { COL_GROUPS, buildSparklineSVG };

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

// ── TA annotation pills ───────────────────────────────────────────────────────

function buildTaAnnotations(c) {
    const pills = [];
    const price = c.price;

    if (c.sma_200 != null) {
        if (price >= c.sma_200) {
            pills.push('<span class="tr-ta-pill ta-green">above 200 sma</span>');
        } else {
            pills.push('<span class="tr-ta-pill ta-red">below 200 sma</span>');
        }
    }

    if (c.bb_upper != null && c.bb_mid != null && c.bb_lower != null) {
        if (price > c.bb_upper) {
            pills.push('<span class="tr-ta-pill ta-green">above bb upper</span>');
        } else if (price >= c.bb_mid) {
            pills.push('<span class="tr-ta-pill ta-blue">above bb mid</span>');
        } else if (price >= c.bb_lower) {
            pills.push('<span class="tr-ta-pill ta-amber">below bb mid</span>');
        } else {
            pills.push('<span class="tr-ta-pill ta-red">below bb lower</span>');
        }
    }

    if (pills.length === 0) return '';
    return `<div class="tr-ta-pills">${pills.join('')}</div>`;
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
            if (col.render) return col.render(c);
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
        <div class="watchlist-view">
        <div class="section-header" style="padding-bottom:8px">
            <div class="watchlist-sub-tabs" id="watchlist-sub-tabs"></div>
            <div style="display:flex;align-items:center;gap:6px">
                <span class="cache-badge" id="cache-status-active"></span>
                <button class="edit-pencil-btn" onclick="renderEditWatchlistView()" title="Edit watchlists">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="m18.5 2.5 3 3L12 15H9v-3L18.5 2.5z"/></svg>
                </button>
            </div>
        </div>
        <div id="watchlist-content"></div>
        </div>`;
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
        <div class="col-header-row" style="grid-template-columns:1fr auto auto auto; gap: 0 16px; padding: 4px 14px 6px">
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
        const yld = c.annualized_roc ? `${parseFloat(c.annualized_roc).toFixed(1)}%y` : '—';
        const tierCls = roc >= 3 ? 'up' : roc >= 1.5 ? 'tier-mid' : '';
        return `<div class="option-card ${tierCls}" style="--row-delay:${i * 20}ms" onclick="openTradingView('${escHtml(c.symbol)}')">
            <div class="oc-row1">
                <span class="oc-symbol">${escHtml(c.symbol)}</span>
                <span class="oc-meta"><span style="color:#fff">$${c.strike.toFixed(2)}</span> · ${c.dte ?? '—'}d · Δ${c.delta != null ? c.delta.toFixed(2) : '—'}</span>
                <span class="oc-highlight">${roc.toFixed(2)}% ROC</span>
            </div>
            <div class="oc-row2">
                <span class="oc-name">$${c.current_price.toFixed(2)} · IV ${c.impliedVolatility != null ? c.impliedVolatility.toFixed(1) + '%' : '—'}</span>
                <span class="oc-metrics"><span class="oc-prem">$${c.premium.toFixed(2)}</span> · <span class="oc-yield">${yld}</span> · ${c.otm_percent}% OTM</span>
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
        <div class="col-header-row" style="grid-template-columns:1fr auto auto auto; gap: 0 16px; padding: 4px 14px 6px">
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

    listEl.innerHTML = slice.map((c, i) => {
        const mkup = parseFloat(c.premium_markup_percent) || 0;
        const tierCls = mkup <= 5 ? 'up' : mkup <= 15 ? 'tier-mid' : '';
        return `<div class="option-card ${tierCls}" style="--row-delay:${i * 20}ms" onclick="openTradingView('${escHtml(c.symbol)}')">
            <div class="oc-row1">
                <span class="oc-symbol">${escHtml(c.symbol)}</span>
                <span class="oc-meta"><span style="color:#fff">$${c.strike.toFixed(2)}</span> · ${c.expiration}</span>
                <span class="oc-highlight">${mkup.toFixed(1)}% mkup</span>
            </div>
            <div class="oc-row2">
                <span class="oc-name">$${c.current_price.toFixed(2)} · vol ${c.volume ?? '—'}</span>
                <span class="oc-metrics"><span class="oc-prem">$${c.premium.toFixed(2)}</span> · BE $${c.break_even.toFixed(2)}</span>
            </div>
        </div>`;
    }).join('');

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

// ── Edit Watchlist view ───────────────────────────────────────────────────────

function renderEditWatchlistView() {
    activeEditTab = 'watchlists';
    stockChipEditor = null;
    cspWlChipEditor = null;
    channelsChipEditor = null;
    document.getElementById('main-content').innerHTML = `
        <div class="watchlist-view">
        <div class="section-header" style="padding-bottom:8px">
            <button class="edit-back-btn" onclick="backToWatchlist()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" width="14" height="14"><polyline points="15 18 9 12 15 6"/></svg>
                WATCHLIST
            </button>
            <span class="section-title">Edit Watchlist</span>
        </div>
        <div class="watchlist-sub-tabs" id="edit-sub-tabs" style="padding: 0 16px 8px"></div>
        <div id="edit-content"></div>
        </div>`;
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

function showEditStatus(el, msg, cls) {
    if (!el) return;
    el.textContent = msg;
    el.className = `edit-status ${cls}`;
    setTimeout(() => { if (el.textContent === msg) { el.textContent = ''; el.className = 'edit-status'; } }, 4000);
}

function initChipEditor(containerEl, initialValues, opts = {}) {
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
                let val = opts.urlMode ? raw : raw.toUpperCase();
                if (opts.urlMode) {
                    if (/^@[\w.-]+$/.test(val)) {
                        val = `https://www.youtube.com/${val}`;
                    } else if (!val.includes('youtube.com/')) {
                        inputEl.classList.add('shake');
                        setTimeout(() => inputEl.classList.remove('shake'), 500);
                        return;
                    }
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

function extractYouTubeHandle(url) {
    if (/^@[\w.-]+$/.test(url)) return url;
    try {
        // /@handle
        const atMatch = url.match(/youtube\.com\/@([\w.-]+)/);
        if (atMatch) return `@${atMatch[1]}`;
        // /c/name or /user/name
        const legacyMatch = url.match(/youtube\.com\/(?:c|user)\/([\w.-]+)/);
        if (legacyMatch) return legacyMatch[1];
        // /channel/UCID — truncate the opaque ID
        const channelMatch = url.match(/youtube\.com\/channel\/([\w-]+)/);
        if (channelMatch) return `${channelMatch[1].slice(0, 11)}…`;
        // bare /Name (no prefix segment)
        const u = new URL(url);
        const parts = u.pathname.replace(/^\//, '').split('/').filter(Boolean);
        return parts[0] || url;
    } catch {
        return url;
    }
}

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

// ── Market Overview view ──────────────────────────────────────────────────────

function renderOverviewView() {
    sectorView = 'etfs';
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
        if (sectorView === 'etfs') renderSectors(data.sectors);
        else renderThemes(data.themes);
        renderVix(data.vix);
        renderGex(data.gex);
        renderBreadth(data.breadth);
    } catch (err) {
        console.error('fetchMarketOverview failed:', err);
        ['sector-bars', 'vix-content', 'gex-content', 'breadth-content'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>';
        });
    }
}

function renderSectors(sectors) {
    const el = document.getElementById('sector-bars');
    if (!el || !sectors) { if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const tf = sectorTf;
    const sorted = Object.entries(sectors).sort(([, a], [, b]) => (b[`pct_${tf}`] ?? -Infinity) - (a[`pct_${tf}`] ?? -Infinity));
    const fmt = pct => pct == null ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    const arrowUp   = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 11V3M7 3L3.5 6.5M7 3L10.5 6.5" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    const arrowDown = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 3V11M7 11L3.5 7.5M7 11L10.5 7.5" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    el.innerHTML =
        `<div class="sector-tf-row">
            <span class="sector-tf-btn${tf==='1d'?' active':''}" data-tf="1d">1D</span>
            <span class="sector-tf-btn${tf==='1w'?' active':''}" data-tf="1w">1W</span>
            <span class="sector-tf-btn${tf==='1m'?' active':''}" data-tf="1m">1M</span>
        </div>
        <div class="sector-pills">` +
        sorted.map(([ticker, s]) => {
            const pct = s[`pct_${tf}`];
            const pos = pct == null || pct >= 0;
            return `
        <div class="sector-pill ${pos ? 'positive' : 'negative'}">
            <div class="sector-pill-icon">${pos ? arrowUp : arrowDown}</div>
            <div class="sector-pill-body">
                <span class="sector-pill-name">${escHtml(s.name)}</span>
                <span class="sector-pill-ticker">${escHtml(ticker)}</span>
            </div>
            <span class="sector-pill-pct">${fmt(pct)}</span>
        </div>`;
        }).join('') +
        `</div>`;
    el.querySelectorAll('.sector-tf-btn').forEach(btn => {
        btn.addEventListener('click', () => _setSectorTf(btn.dataset.tf));
    });
    _setupSectorSwipe(el);
}

function renderThemes(themes) {
    const el = document.getElementById('sector-bars');
    if (!el || !themes) { if (el) el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }

    const { singles = {}, baskets = {} } = themes;
    const tf = sectorTf;

    const items = [];
    for (const [label, d] of Object.entries(singles)) {
        items.push({ label, type: 'single', ticker: d.ticker, pct_1d: d.pct_1d, pct_1w: d.pct_1w, pct_1m: d.pct_1m });
    }
    for (const [label, d] of Object.entries(baskets)) {
        items.push({ label, type: 'basket', tickers: d.tickers, pct_1d: d.avg_1d, pct_1w: d.avg_1w, pct_1m: d.avg_1m });
    }
    items.sort((a, b) => (b[`pct_${tf}`] ?? -Infinity) - (a[`pct_${tf}`] ?? -Infinity));

    const fmt = pct => pct == null ? '—' : `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
    const arrowUp   = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 11V3M7 3L3.5 6.5M7 3L10.5 6.5" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    const arrowDown = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M7 3V11M7 11L3.5 7.5M7 11L10.5 7.5" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const rows = items.map(item => {
        const pct = item[`pct_${tf}`];
        const pos = pct == null || pct >= 0;
        const subLabel = item.type === 'basket'
            ? `<span class="sector-pill-ticker theme-basket-toggle">▶ basket</span>`
            : (item.ticker ? `<span class="sector-pill-ticker">${escHtml(item.ticker)}</span>` : '');

        const mainPill = `
        <div class="sector-pill ${pos ? 'positive' : 'negative'}${item.type === 'basket' ? ' basket-pill' : ''}" data-basket="${item.type === 'basket' ? escHtml(item.label) : ''}">
            <div class="sector-pill-icon">${pos ? arrowUp : arrowDown}</div>
            <div class="sector-pill-body">
                <span class="sector-pill-name">${escHtml(item.label)}</span>
                ${subLabel}
            </div>
            <span class="sector-pill-pct">${fmt(pct)}</span>
        </div>`;

        if (item.type !== 'basket') return mainPill;

        const subPills = Object.entries(item.tickers).map(([t, d]) => {
            const subPct = d[`pct_${tf}`];
            const subPos = subPct == null || subPct >= 0;
            return `
        <div class="sector-pill sector-pill-sub ${subPos ? 'positive' : 'negative'}" data-parent="${escHtml(item.label)}" style="display:none">
            <div class="sector-pill-icon" style="width:22px;height:22px">${subPos ? arrowUp : arrowDown}</div>
            <div class="sector-pill-body">
                <span class="sector-pill-name" style="font-size:12px">${escHtml(t)}</span>
            </div>
            <span class="sector-pill-pct" style="font-size:12px">${fmt(subPct)}</span>
        </div>`;
        }).join('');

        return mainPill + subPills;
    });

    el.innerHTML =
        `<div class="sector-tf-row">
            <span class="sector-tf-btn${tf==='1d'?' active':''}" data-tf="1d">1D</span>
            <span class="sector-tf-btn${tf==='1w'?' active':''}" data-tf="1w">1W</span>
            <span class="sector-tf-btn${tf==='1m'?' active':''}" data-tf="1m">1M</span>
        </div>
        <div class="sector-pills">` + rows.join('') + `</div>`;

    el.querySelectorAll('.sector-tf-btn').forEach(btn => {
        btn.addEventListener('click', () => _setSectorTf(btn.dataset.tf));
    });
    el.querySelectorAll('.basket-pill').forEach(pill => {
        pill.addEventListener('click', () => {
            const label  = pill.dataset.basket;
            const toggle = pill.querySelector('.theme-basket-toggle');
            const subs   = el.querySelectorAll(`.sector-pill-sub[data-parent="${CSS.escape(label)}"]`);
            const isOpen = subs.length > 0 && subs[0].style.display !== 'none';
            subs.forEach(p => p.style.display = isOpen ? 'none' : 'flex');
            if (toggle) toggle.textContent = isOpen ? '▶ basket' : '▼ basket';
        });
    });
    _setupSectorSwipe(el);
}

function _setSectorTf(tf, dir) {
    sectorTf = tf;
    if (sectorView === 'etfs') renderSectors(cachedOverviewData?.sectors);
    else renderThemes(cachedOverviewData?.themes);
    if (dir) {
        const pills = document.querySelector('#sector-bars .sector-pills');
        if (pills) {
            pills.classList.add(`anim-${dir}`);
            setTimeout(() => pills.classList.remove(`anim-${dir}`), 200);
        }
    }
}

function _setupSectorSwipe(el) {
    if (el.dataset.swipeInit) return;
    el.dataset.swipeInit = '1';
    const tfs = ['1d', '1w', '1m'];
    let startX = 0, startY = 0;
    el.addEventListener('touchstart', e => {
        startX = e.touches[0].clientX;
        startY = e.touches[0].clientY;
    }, { passive: true });
    el.addEventListener('touchend', e => {
        const dx = e.changedTouches[0].clientX - startX;
        const dy = e.changedTouches[0].clientY - startY;
        if (Math.abs(dx) < 40 || Math.abs(dy) > Math.abs(dx)) return;
        const idx = tfs.indexOf(sectorTf);
        if (dx < 0 && idx < 2) _setSectorTf(tfs[idx + 1], 'fwd');
        else if (dx > 0 && idx > 0) _setSectorTf(tfs[idx - 1], 'back');
    }, { passive: true });
}

function renderVix(vix) {
    const el = document.getElementById('vix-content');
    if (!el) return;
    if (!vix || vix.spot == null) { el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const fmtChg = pct => {
        if (pct == null) return '<span style="color:var(--tv-muted)">—</span>';
        const color = pct >= 0 ? 'var(--tv-green)' : 'var(--tv-red)';
        return `<span style="color:${color}">${pct >= 0 ? '↑' : '↓'}${Math.abs(pct).toFixed(1)}%</span>`;
    };
    const tsClass = { Contango: 'contango', Backwardation: 'backwardation', Flat: 'flat' }[vix.term_structure] ?? 'flat';
    el.innerHTML = `
        <div class="vix-spot">${vix.spot.toFixed(2)}</div>
        <div class="vix-changes">1D: ${fmtChg(vix.pct_1d)} &nbsp; 1W: ${fmtChg(vix.pct_1w)}</div>
        <div class="vix-term ${tsClass}">Structure: ${escHtml(vix.term_structure)} — ${escHtml(vix.stress_note)} (spread ${vix.spread >= 0 ? '+' : ''}${vix.spread.toFixed(2)})</div>`;
}

function renderGex(gex) {
    const el = document.getElementById('gex-content');
    if (!el) return;
    if (!gex || gex.value_b == null) { el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const arrow = gex.trend === 'Rising' ? '↑' : gex.trend === 'Falling' ? '↓' : '→';
    const sign = gex.value_b >= 0 ? 'positive' : 'negative';
    el.innerHTML = `
        <div class="gex-value ${sign}">$${gex.value_b.toFixed(1)}B</div>
        <div class="gex-label">${escHtml(gex.label)}</div>
        <div class="gex-avg">20d avg: $${gex.rolling_20d_avg_b.toFixed(1)}B &nbsp; ${arrow} ${escHtml(gex.trend)}</div>`;
}

function renderBreadth(breadth) {
    const el = document.getElementById('breadth-content');
    if (!el) return;
    if (!breadth) { el.innerHTML = '<span style="color:var(--tv-muted);font-size: 13px">Unavailable</span>'; return; }
    const pct = breadth.pct_above_200ma ?? 0;
    const maColor = pct >= 60 ? 'green' : pct >= 40 ? 'yellow' : 'red';
    const ratio = breadth.ad_ratio;
    const adAgreement = (pct > 50 && ratio != null && ratio > 1) ? 'positive'
        : (pct < 50 && ratio != null && ratio < 1) ? 'negative'
        : '';
    el.innerHTML = `
        <div class="breadth-row">
            <span class="breadth-label">200d MA</span>
            <div class="breadth-bar-track"><div class="breadth-bar-fill ${maColor}" style="width:${pct.toFixed(1)}%"></div></div>
            <span class="breadth-value ${maColor}">${pct.toFixed(0)}%</span>
        </div>
        <div class="breadth-ad ${adAgreement}">A/D &nbsp; ${breadth.advancing}↑ / ${breadth.declining}↓ &nbsp; ratio ${ratio != null ? ratio.toFixed(2) : '—'}</div>`;
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    renderBottomNav();
    fetchMarketPosture();
    switchTab('watchlist');
});
