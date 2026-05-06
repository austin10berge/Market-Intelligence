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
    console.error(
        "[Market Intelligence] FATAL: window.MARKET_INTELLIGENCE_CONFIG is not defined.\n" +
        "config.js was not loaded. The app cannot reach the API from a remote device.\n" +
        "Ensure <script src='/config.js'> is in index.html and the dashboard container is rebuilt."
    );
    // Return a clearly broken URL so fetch errors are obvious in the console
    return "/MISSING_CONFIG_JS_SEE_CONSOLE";
})();

// CSP State
let allCspCandidates = [];
let currentCspSort = 'annualized_roc';
let currentCspSortDesc = true;
let currentCspPage = 1;
const CSP_PER_PAGE = 7;

// LEAPS State
let allLeapsCandidates = [];
let currentLeapsPage = 1;
const LEAPS_PER_PAGE = 7; // ← change this number to show more/fewer rows per page

// Stock State
let allStockCandidates = [];
let currentStockSort = { column: 'pct_1d', asc: false };

/**
 * Build a tiny inline SVG sparkline from an array of price values.
 * Green stroke if the stock is up over the period, red if down.
 */
function buildSparklineSVG(prices, isUp) {
    if (!prices || prices.length < 2) return '<div class="sparkline-empty">—</div>';

    const width = 60;
    const height = 24;
    const padding = 2;
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const range = max - min || 1;

    const points = prices.map((p, i) => {
        const x = padding + (i / (prices.length - 1)) * (width - padding * 2);
        const y = padding + (1 - (p - min) / range) * (height - padding * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');

    const color = isUp ? '#34d399' : '#f87171';

    return `<svg class="sparkline-svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
        <polyline fill="none" stroke="${color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" points="${points}" />
    </svg>`;
}

document.addEventListener("DOMContentLoaded", () => {
    initDashboard();
    initScrollOptimization();
});

function initScrollOptimization() {
    let isScrolling;
    window.addEventListener('scroll', () => {
        window.clearTimeout(isScrolling);
        document.body.classList.add('disable-hover');

        isScrolling = setTimeout(() => {
            document.body.classList.remove('disable-hover');
        }, 150);
    }, { passive: true });
}

async function initDashboard() {
    // Fire off all requests concurrently
    Promise.allSettled([
        fetchMarketPosture(),
        fetchMarketOverview(),
        fetchCspCandidates(),
        fetchLeapsCandidates(),
        fetchStockScreener()
    ]);
}

async function fetchMarketPosture() {
    try {
        const response = await fetch(`${API_BASE}/market-posture`);
        if (!response.ok) throw new Error("Failed to fetch posture");
        const data = await response.json();

        renderPosture(data);
    } catch (err) {
        console.error(err);
        document.getElementById("posture-widget").innerText = "Error Loading Data";
        document.getElementById("posture-widget").classList.remove("loading");
    }
}

async function fetchCspCandidates() {
    try {
        const response = await fetch(`${API_BASE}/screener/csp`);
        if (!response.ok) throw new Error("Failed to fetch CSPs");
        const data = await response.json();
        allCspCandidates = data.candidates;
        updateCacheStatus('csp', data);
        sortAndRenderCsp();
    } catch (err) {
        console.error(err);
        document.getElementById("csp-list").innerHTML = "<div class='trade-item'>Error fetching option chains</div>";
    }
}

async function fetchLeapsCandidates() {
    try {
        const response = await fetch(`${API_BASE}/screener/leaps`);
        if (!response.ok) throw new Error("Failed to fetch LEAPS");
        const data = await response.json();
        allLeapsCandidates = data.candidates;
        updateCacheStatus('leaps', data);
        currentLeapsPage = 1;
        renderLeapsPage();
    } catch (err) {
        console.error(err);
        document.getElementById("leaps-list").innerHTML = "<div class='trade-item'>Error fetching option chains</div>";
    }
}

async function fetchStockScreener() {
    const listEl = document.getElementById("stocks-list");
    listEl.innerHTML = "<div class='trade-item' style='justify-content:center'>Scanning stock data...</div>";
    try {
        const res = await fetch(`${API_BASE}/screener/stocks`);
        if (!res.ok) throw new Error("Failed");
        const data = await res.json();
        allStockCandidates = data.candidates || [];
        updateCacheStatus('stocks', data);
        listEl.classList.remove("loading");
        sortStocks(currentStockSort.column, currentStockSort.asc);
    } catch (e) {
        listEl.classList.remove("loading");
        listEl.innerHTML = "<div class='trade-item'>Error fetching stocks</div>";
    }
}

function renderPosture(data) {
    const postureWidget = document.getElementById("posture-widget");
    const compositeEl = document.getElementById("composite-score");

    postureWidget.classList.remove("loading", "posture-bullish", "posture-bearish", "posture-neutral");

    // Clean string formatting
    const postureTxt = data.posture || "Neutral";
    postureWidget.innerHTML = `<span class="pulse-ring"></span> ${postureTxt}`;

    if (postureTxt.includes("Bullish")) {
        postureWidget.classList.add("posture-bullish");
    } else if (postureTxt.includes("Bearish")) {
        postureWidget.classList.add("posture-bearish");
    } else {
        postureWidget.classList.add("posture-neutral");
    }

    // Score
    const compositeScore = parseFloat(data.composite_score).toFixed(3);
    const sign = compositeScore > 0 ? "+" : "";
    compositeEl.innerText = `Composite Signal: ${sign}${compositeScore}`;
}

async function fetchMarketOverview() {
    try {
        const res = await fetch(`${API_BASE}/market-overview`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderSectors(data.sectors);
        renderVix(data.vix);
        renderGex(data.gex);
        renderBreadth(data.breadth);
    } catch (err) {
        console.error('fetchMarketOverview failed:', err);
        ['sector-bars', 'vix-content', 'gex-content', 'breadth-content'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.innerHTML = '<div class="loading-placeholder">Unavailable</div>';
        });
    }
}

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderSectors(sectors) {
    const el = document.getElementById('sector-bars');
    if (!el) return;
    if (!sectors) { el.innerHTML = '<div class="loading-placeholder">Unavailable</div>'; return; }

    // Sort by 1D % descending (nulls last)
    const sorted = Object.entries(sectors).sort(([, a], [, b]) => {
        const av = a.pct_1d ?? -Infinity;
        const bv = b.pct_1d ?? -Infinity;
        return bv - av;
    });

    const vals1d = sorted.map(([, s]) => s.pct_1d).filter(v => v != null);
    const vals1w = sorted.map(([, s]) => s.pct_1w).filter(v => v != null);
    const vals1m = sorted.map(([, s]) => s.pct_1m).filter(v => v != null);
    const max1d = vals1d.length ? Math.max(...vals1d.map(Math.abs)) : 1;
    const max1w = vals1w.length ? Math.max(...vals1w.map(Math.abs)) : 1;
    const max1m = vals1m.length ? Math.max(...vals1m.map(Math.abs)) : 1;

    function barWidth(pct, maxAbs) {
        if (pct == null || maxAbs === 0) return 0;
        return Math.abs(pct) / maxAbs * 50;
    }

    function pctClass(pct) {
        if (pct == null) return 'neutral';
        return pct >= 0 ? 'positive' : 'negative';
    }

    function fmtPct(pct) {
        if (pct == null) return '—';
        return (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
    }

    const header = `
    <div class="sector-bar-row sector-bar-header">
      <span></span>
      <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1D</span>
      <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1W</span>
      <span class="sector-timeframe-label" style="grid-column:span 2;text-align:center">1M</span>
    </div>`;

    el.innerHTML = header + sorted.map(([ticker, s]) => `
    <div class="sector-bar-row">
      <span class="sector-label" title="${escHtml(ticker)}">${escHtml(s.name)}</span>
      <div class="sector-bar-cell">
        <div class="sector-bar ${pctClass(s.pct_1d)}" style="width:${barWidth(s.pct_1d, max1d)}%"></div>
      </div>
      <span class="sector-pct ${pctClass(s.pct_1d)}">${fmtPct(s.pct_1d)}</span>
      <div class="sector-bar-cell">
        <div class="sector-bar ${pctClass(s.pct_1w)} opacity-1w" style="width:${barWidth(s.pct_1w, max1w)}%"></div>
      </div>
      <span class="sector-pct ${pctClass(s.pct_1w)}">${fmtPct(s.pct_1w)}</span>
      <div class="sector-bar-cell">
        <div class="sector-bar ${pctClass(s.pct_1m)}" style="width:${barWidth(s.pct_1m, max1m)}%"></div>
      </div>
      <span class="sector-pct ${pctClass(s.pct_1m)}">${fmtPct(s.pct_1m)}</span>
    </div>
  `).join('');
}

function renderVix(vix) {
    const el = document.getElementById('vix-content');
    if (!el) return;
    if (!vix) { el.innerHTML = '<div class="loading-placeholder">Unavailable</div>'; return; }

    if (vix.spot == null || vix.spread == null) {
      el.innerHTML = '<div class="loading-placeholder">Unavailable</div>';
      return;
    }

    function fmtVixPct(pct) {
        if (pct == null) return '<span style="color:var(--text-muted,#888)">—</span>';
        const arrow = pct >= 0 ? '↑' : '↓';
        const color = pct >= 0 ? 'var(--accent-green,#4caf50)' : 'var(--accent-red,#f44336)';
        return `<span style="color:${color}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
    }

    const tsMap = { 'Contango': 'contango', 'Backwardation': 'backwardation', 'Flat': 'flat' };
    const tsClass = tsMap[vix.term_structure] ?? 'flat';

    el.innerHTML = `
    <div class="vix-spot">${vix.spot.toFixed(2)}</div>
    <div class="vix-changes">
      1D: ${fmtVixPct(vix.pct_1d)}
      &nbsp;&nbsp;1W: ${fmtVixPct(vix.pct_1w)}
    </div>
    <div class="vix-term ${tsClass}">
      ${escHtml(vix.term_structure)} — ${escHtml(vix.stress_note)}
      <span style="opacity:0.7">(spread ${vix.spread >= 0 ? '+' : ''}${vix.spread.toFixed(2)})</span>
    </div>
  `;
}

function renderGex(gex) {
    const el = document.getElementById('gex-content');
    if (!el) return;
    if (!gex) { el.innerHTML = '<div class="loading-placeholder">Unavailable</div>'; return; }

    if (gex.value_b == null || gex.rolling_20d_avg_b == null) {
      el.innerHTML = '<div class="loading-placeholder">Unavailable</div>';
      return;
    }

    const trendArrow = gex.trend === 'Rising' ? '↑' : gex.trend === 'Falling' ? '↓' : '→';

    el.innerHTML = `
    <div class="gex-value">$${gex.value_b.toFixed(1)}B</div>
    <div class="gex-label">${gex.label}</div>
    <div class="gex-avg">20d avg: $${gex.rolling_20d_avg_b.toFixed(1)}B &nbsp; ${trendArrow} ${gex.trend}</div>
  `;
}

function renderBreadth(breadth) {
    const el = document.getElementById('breadth-content');
    if (!el) return;
    if (!breadth) { el.innerHTML = '<div class="loading-placeholder">Unavailable</div>'; return; }

    const pct = breadth.pct_above_200ma ?? 0;
    const maColor = pct >= 60 ? 'green' : pct >= 40 ? 'yellow' : 'red';
    const adColor = (breadth.ad_ratio ?? 0) >= 1.2 ? 'green' : (breadth.ad_ratio ?? 0) >= 0.8 ? 'yellow' : 'red';

    el.innerHTML = `
    <div class="breadth-row">
      <span class="breadth-label">200d MA</span>
      <div class="breadth-bar-track">
        <div class="breadth-bar-fill ${maColor}" style="width:${pct.toFixed(1)}%"></div>
      </div>
      <span class="breadth-value ${maColor}">${pct.toFixed(0)}%</span>
    </div>
    <div class="breadth-ad">
      A/D &nbsp; ${breadth.advancing} ↑ / ${breadth.declining} ↓
      &nbsp; ratio ${breadth.ad_ratio != null ? breadth.ad_ratio.toFixed(2) : '—'}
    </div>
  `;
}

function renderCspCandidates(candidates) {
    const list = document.getElementById("csp-list");
    list.classList.remove("loading");

    if (!candidates || candidates.length === 0) {
        list.innerHTML = "<div class='trade-item'>No viable candidates found.</div>";
        return;
    }

    list.innerHTML = candidates.map(c => `
            <div class="trade-item" onclick="openTradingView('${c.symbol}')" title="Open ${c.symbol} on TradingView" style="cursor:pointer">
                <div class="ticker-block">
                    <span class="ticker">${c.symbol}</span>
                    <span class="ticker-sub">Stock: ${c.current_price.toFixed(2)}</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.strike.toFixed(2)}</span>
                    <span class="m-lbl">Strike</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.premium.toFixed(2)}</span>
                    <span class="m-lbl">Premium</span>
                </div>
                <div class="metric">
                    <span class="m-val highlight">${c.roc_percent}%</span>
                    <span class="m-lbl">ROC</span>
                </div>
                <div class="metric">
                    <span class="m-val highlight">${c.annualized_roc ? c.annualized_roc + '%' : '—'}</span>
                    <span class="m-lbl">Yield (Ann.)</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.otm_percent}%</span>
                    <span class="m-lbl">% OTM</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.delta != null ? c.delta.toFixed(2) : '—'}</span>
                    <span class="m-lbl">Delta</span>
                </div>
                <div class="metric">
                    <span class="m-val ${c.spread_pct > 10 ? 'text-red' : ''}">${c.spread_pct > 0 ? c.spread_pct.toFixed(1) + '%' : '—'}</span>
                    <span class="m-lbl">Spread %</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.impliedVolatility > 0 ? c.impliedVolatility.toFixed(1) + '%' : '—'}</span>
                    <span class="m-lbl">IV</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.volume > 0 ? c.volume.toLocaleString() : '—'}</span>
                    <span class="m-lbl">Volume</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.dte ?? '—'}</span>
                    <span class="m-lbl">DTE</span>
                </div>
            </div>
        `).join("");
}

// ── LEAPS Pagination & Sorting ─────────────────────────────────

let currentLeapsSort = 'premium_markup_percent';
let currentLeapsSortDesc = false; // lowest markup first by default

function sortLeaps(field) {
    if (currentLeapsSort === field) {
        currentLeapsSortDesc = !currentLeapsSortDesc;
    } else {
        currentLeapsSort = field;
        // Expiration sorts ascending by default (nearest first)
        currentLeapsSortDesc = field === 'expiration' ? false : true;
    }
    currentLeapsPage = 1;
    renderLeapsPage();
}

function prevLeapsPage() {
    if (currentLeapsPage > 1) {
        currentLeapsPage--;
        renderLeapsPage();
    }
}

function nextLeapsPage() {
    const totalPages = Math.ceil(allLeapsCandidates.length / LEAPS_PER_PAGE);
    if (currentLeapsPage < totalPages) {
        currentLeapsPage++;
        renderLeapsPage();
    }
}

function renderLeapsPage() {
    // Sort array before slicing
    allLeapsCandidates.sort((a, b) => {
        let valA = a[currentLeapsSort];
        let valB = b[currentLeapsSort];
        if (typeof valA === 'string') {
            return currentLeapsSortDesc ? valB.localeCompare(valA) : valA.localeCompare(valB);
        }
        return currentLeapsSortDesc ? valB - valA : valA - valB;
    });

    const startIndex = (currentLeapsPage - 1) * LEAPS_PER_PAGE;
    const pageItems = allLeapsCandidates.slice(startIndex, startIndex + LEAPS_PER_PAGE);

    // Update pagination controls
    const totalPages = Math.ceil(allLeapsCandidates.length / LEAPS_PER_PAGE) || 1;
    document.getElementById("leaps-page-info").innerText = `Page ${currentLeapsPage} / ${totalPages}`;
    document.getElementById("leaps-prev").disabled = currentLeapsPage === 1;
    document.getElementById("leaps-next").disabled = currentLeapsPage === totalPages;

    renderLeapsCandidates(pageItems);
}

function renderLeapsCandidates(candidates) {
    const list = document.getElementById("leaps-list");
    list.classList.remove("loading");

    if (!candidates || candidates.length === 0) {
        list.innerHTML = "<div class='trade-item'>No viable candidates found.</div>";
        return;
    }

    list.innerHTML = candidates.map(c => `
            <div class="trade-item" onclick="openTradingView('${c.symbol}')" title="Open ${c.symbol} on TradingView" style="cursor:pointer">
                <div class="ticker-block">
                    <span class="ticker">${c.symbol}</span>
                    <span class="ticker-sub">Stock: ${c.current_price.toFixed(2)}</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.strike.toFixed(2)}</span>
                    <span class="m-lbl">Strike</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.premium.toFixed(2)}</span>
                    <span class="m-lbl">Premium</span>
                </div>
                <div class="metric">
                    <span class="m-val highlight">${c.premium_markup_percent.toFixed(1)}%</span>
                    <span class="m-lbl">Markup</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.volume > 0 ? c.volume.toLocaleString() : '—'}</span>
                    <span class="m-lbl">Volume</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.expiration}</span>
                    <span class="m-lbl">Expiration</span>
                </div>
                <div class="metric">
                    <span class="m-val">${c.break_even.toFixed(2)}</span>
                    <span class="m-lbl">Break Even</span>
                </div>
            </div>
        `).join("");
}

function renderStockCandidates(candidates) {
    const listEl = document.getElementById("stocks-list");
    if (!candidates || candidates.length === 0) {
        listEl.innerHTML = "<div class='trade-item' style='justify-content:center'>No stock data</div>";
        return;
    }

    listEl.innerHTML = candidates.map(c => {
        const d1Class = c.pct_1d >= 0 ? 'text-green' : 'text-red';
        const d1Sign = c.pct_1d >= 0 ? '+' : '';
        const w1Class = c.pct_1w >= 0 ? 'text-green' : 'text-red';
        const w1Sign = c.pct_1w >= 0 ? '+' : '';
        const m1Class = c.pct_1m >= 0 ? 'text-green' : 'text-red';
        const m1Sign = c.pct_1m >= 0 ? '+' : '';
        const ivRv20Display = c.atm_iv_rv20 === 'N/A' ? 'N/A' : c.atm_iv_rv20.toFixed(2);
        const ivPctDisplay = c.iv_percentile === 'N/A' ? 'N/A' : `${c.iv_percentile.toFixed(2)}%`;
        const ivHistoryPoints = Number.isFinite(c.iv_history_points) ? c.iv_history_points : 0;
        const ivPctTooltip = c.iv_percentile === 'N/A'
            ? `IV Percentile unavailable. History points: ${ivHistoryPoints}`
            : `IV was below current level ${c.iv_percentile.toFixed(1)}% of the time over ${ivHistoryPoints} trading days`;
        const ivRv20Tooltip = c.atm_iv_rv20 === 'N/A'
            ? 'IV/RV20 unavailable'
            : 'Higher values mean options are pricing more movement than the stock has recently realized';

        return `
            <div class="trade-item" onclick="openTradingView('${c.symbol}')" title="Open ${c.symbol} on TradingView" style="cursor:pointer">
                <div class="ticker-block">
                    <span class="symbol">${c.symbol}</span>
                </div>
                <div class="metric stock-name" data-label="Name">${c.name}</div>
                <div class="metric sparkline-cell">${buildSparklineSVG(c.price_history_1m, c.pct_1m >= 0)}</div>
                <div class="metric" data-label="Price"><span class="m-val">$${c.price.toFixed(2)}</span></div>
                <div class="metric" data-label="Sector" style="font-size: 0.8rem;">${c.sector}</div>
                <div class="metric ${d1Class}" data-label="1D">${d1Sign}${c.pct_1d.toFixed(2)}%</div>
                <div class="metric ${w1Class}" data-label="1W">${w1Sign}${c.pct_1w.toFixed(2)}%</div>
                <div class="metric ${m1Class}" data-label="1M">${m1Sign}${c.pct_1m.toFixed(2)}%</div>
                <div class="metric" data-label="P/E">${c.pe}</div>
                <div class="metric" data-label="Beta">${c.beta}</div>
                <div class="metric" data-label="IV/RV20" title="${ivRv20Tooltip}">${ivRv20Display}</div>
                <div class="metric" data-label="IV Pct" title="${ivPctTooltip}">${ivPctDisplay}</div>
            </div>
        `;
    }).join("");
}

// ── CSP Pagination & Sorting ──────────────────────────

function sortCsp(field) {
    if (currentCspSort === field) {
        currentCspSortDesc = !currentCspSortDesc;
    } else {
        currentCspSort = field;
        currentCspSortDesc = true;
    }
    currentCspPage = 1; // Reset to page 1 on sort
    sortAndRenderCsp();
}

function prevCspPage() {
    if (currentCspPage > 1) {
        currentCspPage--;
        sortAndRenderCsp();
    }
}

function nextCspPage() {
    const totalPages = Math.ceil(allCspCandidates.length / CSP_PER_PAGE);
    if (currentCspPage < totalPages) {
        currentCspPage++;
        sortAndRenderCsp();
    }
}

function sortAndRenderCsp() {
    // Sort array
    allCspCandidates.sort((a, b) => {
        let valA = a[currentCspSort];
        let valB = b[currentCspSort];

        // Handle string comparison for symbol
        if (typeof valA === 'string') {
            return currentCspSortDesc
                ? valB.localeCompare(valA)
                : valA.localeCompare(valB);
        }

        return currentCspSortDesc ? valB - valA : valA - valB;
    });

    // Paginate
    const startIndex = (currentCspPage - 1) * CSP_PER_PAGE;
    const paginatedItems = allCspCandidates.slice(startIndex, startIndex + CSP_PER_PAGE);

    // Update pagination UI
    const totalPages = Math.ceil(allCspCandidates.length / CSP_PER_PAGE) || 1;
    document.getElementById("csp-page-info").innerText = `Page ${currentCspPage} / ${totalPages}`;
    document.getElementById("csp-prev").disabled = currentCspPage === 1;
    document.getElementById("csp-next").disabled = currentCspPage === totalPages;

    renderCspCandidates(paginatedItems);
}

function sortStocks(column, forceAsc = null) {
    if (forceAsc !== null) {
        currentStockSort.asc = forceAsc;
    } else {
        currentStockSort.asc = currentStockSort.column === column ? !currentStockSort.asc : false;
    }
    currentStockSort.column = column;

    allStockCandidates.sort((a, b) => {
        let valA = a[column];
        let valB = b[column];

        if (typeof valA === 'string' && valA === "N/A") return 1;
        if (typeof valB === 'string' && valB === "N/A") return -1;
        if (typeof valA === 'string' && column !== "symbol" && column !== "name" && column !== "sector") {
            valA = parseFloat(valA) || 0; valB = parseFloat(valB) || 0;
        }

        if (typeof valA === 'string') {
            return currentStockSort.asc ? valA.localeCompare(valB) : valB.localeCompare(valA);
        } else {
            return currentStockSort.asc ? valA - valB : valB - valA;
        }
    });

    renderStockCandidates(allStockCandidates);
}

// ── Cache status display ──────────────────────────────────────────────────────

/**
 * Update the "Last updated" timestamp chip for a given section.
 * Called after each screener fetch with the raw API response.
 *
 * @param {'stocks'|'csp'|'leaps'} section
 * @param {object} apiResponse  - The full API response object (contains cached_at, market_status)
 */
function updateCacheStatus(section, apiResponse) {
    const elId = `cache-status-${section}`;
    const el = document.getElementById(elId);
    if (!el) return;

    const cachedAt = apiResponse.cached_at;       // ISO string or null
    const marketStatus = apiResponse.market_status || '';  // 'Market Open' | 'Market Closed'
    const isCached = apiResponse.cached === true;

    if (!cachedAt) {
        el.textContent = 'Live data';
        el.className = 'cache-badge live';
        return;
    }

    // Format relative time
    const ageMs = Date.now() - new Date(cachedAt).getTime();
    const ageMins = Math.round(ageMs / 60000);
    let ageLabel;
    if (ageMins < 1) {
        ageLabel = 'just now';
    } else if (ageMins === 1) {
        ageLabel = '1 min ago';
    } else if (ageMins < 60) {
        ageLabel = `${ageMins} mins ago`;
    } else {
        const ageHrs = Math.round(ageMins / 60);
        ageLabel = `${ageHrs}h ago`;
    }

    const isOpen = marketStatus === 'Market Open';
    el.textContent = `Updated ${ageLabel} · ${marketStatus}`;
    el.className = `cache-badge ${isOpen ? 'market-open' : 'market-closed'}`;
    el.title = `Data cached at ${new Date(cachedAt).toLocaleTimeString()}`;
}
