// src/web/v2/scanner.js — v2 CSP Universe Scanner (native port of /scanner.js)
// Exposes window.ScannerView.{render, teardown}. Reuses /api/screener/csp-scan*.
(function () {
    'use strict';

    const API_BASE = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || (() => {
        console.error('[Market Intelligence] FATAL: window.MARKET_INTELLIGENCE_CONFIG is not defined.');
        return '/MISSING_CONFIG_JS_SEE_CONSOLE';
    })();
    const SCANNER_PARAMS_KEY = 'market-intelligence:csp-scanner-params';

    const DEFAULT_PARAMS = {
        min_cap: 10, max_price: 150, min_beta: 0.8, max_beta: 2.4, min_vol: 30,
        rsi_max: 50, adx_min: 15, adx_max: 50, dte_min: 3, dte_max: 46,
        conditions: [],
        min_fcf_b: 0, max_debt_to_equity: 2.0, min_revenue_growth: -0.10,
        min_earnings_growth: null, min_dividend_yield: null,
        restrict_to_watchlist_universe: false, sectors: [],
    };

    const _state = {
        params: null,            // filled by _restoreParams() over defaults (Task 2)
        availableConditions: [],
        availableSectors: [],
        freshness: null,
        scanPollId: null,
        scanStart: null,
        candidates: [],
        cspPage: 1,
        lastResult: null,
        // Stock Performance table (mirrors the Watchlist Tickers tab)
        perfStocks: [],
        perfColGroup: 0,
        perfSort: { column: 'pct_1d', asc: false },
        namesBySymbol: {},
    };
    const CSP_PER_PAGE = 7;

    // ── Param config — drives badge rendering ─────────────────────────────────────

    const PARAM_CONFIG = [
        { key: 'min_cap',   label: 'Cap >',      suffix: 'B',  min: 1,   max: 500,  step: 1,   decimals: 0 },
        { key: 'max_price', label: 'Price <',    suffix: '$',  min: 10,  max: 500,  step: 5,   decimals: 0, prefix: '$' },
        { key: 'min_beta',  label: 'Beta min',   suffix: '',   min: 0.1, max: 3.0,  step: 0.1, decimals: 1 },
        { key: 'max_beta',  label: 'Beta max',   suffix: '',   min: 0.5, max: 5.0,  step: 0.1, decimals: 1 },
        { key: 'min_vol',   label: 'Vol ≥',      suffix: '%',  min: 10,  max: 100,  step: 5,   decimals: 0 },
        { key: 'rsi_max',   label: 'RSI <',      suffix: '',   min: 10,  max: 100,  step: 1,   decimals: 0 },
        { key: 'adx_min',   label: 'ADX ≥',      suffix: '',   min: 0,   max: 100,  step: 1,   decimals: 0 },
        { key: 'adx_max',   label: 'ADX ≤',      suffix: '',   min: 0,   max: 100,  step: 1,   decimals: 0 },
        { key: 'dte_min',   label: 'DTE ≥',      suffix: 'd',  min: 1,   max: 90,   step: 1,   decimals: 0 },
        { key: 'dte_max',   label: 'DTE ≤',      suffix: 'd',  min: 1,   max: 180,  step: 1,   decimals: 0 },
        { key: 'min_fcf_b',          label: 'FCF >',  suffix: 'B',  min: -50,  max: 500,  step: 1,   decimals: 1, nullable: true },
        { key: 'max_debt_to_equity',  label: 'D/E <',  suffix: '',   min: 0,    max: 500,  step: 1,   decimals: 0, nullable: true },
        { key: 'min_revenue_growth',  label: 'Rev >',  suffix: '%',  min: -100, max: 100,  step: 1,   decimals: 0, scale: 100, nullable: true },
        { key: 'min_earnings_growth', label: 'EPS >',  suffix: '%',  min: -100, max: 100,  step: 1,   decimals: 0, scale: 100, nullable: true },
        { key: 'min_dividend_yield',  label: 'Div >',  suffix: '%',  min: 0,    max: 20,   step: 0.1, decimals: 1, scale: 100, nullable: true },
    ];

    // ── localStorage helpers ──────────────────────────────────────────────────────

    function _restoreParams() {
        _state.params = { ...DEFAULT_PARAMS, conditions: [], sectors: [] };
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
            if (typeof saved.min_fcf_b           === 'number' || saved.min_fcf_b           === null) p.min_fcf_b           = saved.min_fcf_b;
            if (typeof saved.max_debt_to_equity  === 'number' || saved.max_debt_to_equity  === null) p.max_debt_to_equity  = saved.max_debt_to_equity;
            if (typeof saved.min_revenue_growth  === 'number' || saved.min_revenue_growth  === null) p.min_revenue_growth  = saved.min_revenue_growth;
            if (typeof saved.min_earnings_growth === 'number' || saved.min_earnings_growth === null) p.min_earnings_growth = saved.min_earnings_growth;
            if (typeof saved.min_dividend_yield  === 'number' || saved.min_dividend_yield  === null) p.min_dividend_yield  = saved.min_dividend_yield;
            if (Array.isArray(saved.conditions)) p.conditions = saved.conditions;
            if (typeof saved.restrict_to_watchlist_universe === 'boolean') p.restrict_to_watchlist_universe = saved.restrict_to_watchlist_universe;
            if (Array.isArray(saved.sectors)) p.sectors = saved.sectors;
        } catch { /* corrupt storage — use defaults */ }
    }

    function _persistParams() {
        try { window.localStorage.setItem(SCANNER_PARAMS_KEY, JSON.stringify(_state.params)); } catch {}
    }

    // ── State-only data loaders (no DOM writes) ───────────────────────────────────

    async function loadAvailableConditions() {
        try {
            const res = await fetch(`${API_BASE}/screener/csp-scan/conditions`);
            if (!res.ok) return;
            const data = await res.json();
            _state.availableConditions = data.conditions || [];
        } catch (e) { console.warn('Could not load conditions:', e); }
    }

    async function _loadSectors() {
        try {
            const res = await fetch(`${API_BASE}/screener/csp-scan/sectors`);
            if (!res.ok) return;
            const data = await res.json();
            _state.availableSectors = data.sectors || [];
        } catch (e) { console.warn('Could not load sectors:', e); }
    }

    async function loadDataFreshness() {
        try {
            const res = await fetch(`${API_BASE}/market-data/status`);
            if (!res.ok) { _state.freshness = null; return; }
            _state.freshness = await res.json();   // { is_stale, stale_hours, ... }
        } catch { _state.freshness = null; }
    }

    // ── Build query params from current state ─────────────────────────────────────

    function _buildQueryString() {
        const p = _state.params;
        const qs = new URLSearchParams({
            min_cap:   p.min_cap,
            max_price: p.max_price,
            min_beta:  p.min_beta,
            max_beta:  p.max_beta,
            min_vol:   p.min_vol,
            max_rsi:   p.rsi_max,
            min_adx:   p.adx_min,
            max_adx:   p.adx_max,
            min_dte:   p.dte_min,
            max_dte:   p.dte_max,
        });
        if (p.conditions.length) qs.set('conditions', p.conditions.join(','));
        if (p.min_fcf_b           !== null && p.min_fcf_b           !== undefined) qs.set('min_fcf_b',           p.min_fcf_b);
        if (p.max_debt_to_equity  !== null && p.max_debt_to_equity  !== undefined) qs.set('max_debt_to_equity',  p.max_debt_to_equity);
        if (p.min_revenue_growth  !== null && p.min_revenue_growth  !== undefined) qs.set('min_revenue_growth',  p.min_revenue_growth);
        if (p.min_earnings_growth !== null && p.min_earnings_growth !== undefined) qs.set('min_earnings_growth', p.min_earnings_growth);
        if (p.min_dividend_yield  !== null && p.min_dividend_yield  !== undefined) qs.set('min_dividend_yield',  p.min_dividend_yield);
        if (_state.params.restrict_to_watchlist_universe) qs.set('restrict_to_watchlist_universe', 'true');
        if (_state.params.sectors && _state.params.sectors.length) qs.set('sectors', _state.params.sectors.join(','));
        return qs.toString();
    }

    // ── HTML escape helper (module-local — app.js escHtml is in a different IIFE) ─

    function _escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // ── Header renderer — freshness badge + filter count on Filters button ───────

    function _renderHeader() {
        const badge = document.getElementById('scn-freshness');
        if (!badge) return;
        const f = _state.freshness;
        if (!f) { badge.textContent = 'data: unknown'; badge.className = 'data-freshness-badge empty'; }
        else if (f.is_stale) { badge.textContent = `data ${Math.round(f.stale_hours)}h old`; badge.className = 'data-freshness-badge stale'; }
        else { badge.textContent = 'data fresh'; badge.className = 'data-freshness-badge fresh'; }
        const n = _activeFilterCount();
        const btn = document.getElementById('scn-filters-btn');
        if (btn) {
            btn.textContent = n ? `Filters · ${n}` : 'Filters';
            btn.classList.toggle('has-filters', n > 0);
        }
    }

    // ── Active-filter count ───────────────────────────────────────────────────────

    function _activeFilterCount() {
        let n = 0;
        for (const cfg of PARAM_CONFIG) { if (_state.params[cfg.key] !== DEFAULT_PARAMS[cfg.key]) n++; }
        n += _state.params.conditions.length;
        n += _state.params.sectors.length;
        if (_state.params.restrict_to_watchlist_universe) n++;
        return n;
    }

    // ── Condition id → human label ────────────────────────────────────────────────

    function _conditionLabel(id) {
        const c = _state.availableConditions.find(c => c.id === id);
        return c ? c.label : id;
    }

    // ── Active-params chip row ────────────────────────────────────────────────────

    function _renderActiveParams() {
        const el = document.getElementById('scn-active-params');
        if (!el) return;
        const chips = [];
        for (const cfg of PARAM_CONFIG) {
            const v = _state.params[cfg.key];
            if (v == null || v === DEFAULT_PARAMS[cfg.key]) continue;
            const shown = cfg.scale ? (v * cfg.scale) : v;
            chips.push(`<span class="scn-chip">${cfg.label} ${shown}${cfg.suffix || ''}</span>`);
        }
        _state.params.conditions.forEach(id => chips.push(`<span class="scn-chip">${_escapeHtml(_conditionLabel(id))}</span>`));
        _state.params.sectors.forEach(s => chips.push(`<span class="scn-chip">${_escapeHtml(s)}</span>`));
        if (_state.params.restrict_to_watchlist_universe) chips.push('<span class="scn-chip">S&amp;P+NDX only</span>');
        el.innerHTML = chips.length ? chips.join('') : '<span class="scn-chip muted">Default filters</span>';
    }

    // ── Time helper ───────────────────────────────────────────────────────────────

    function _timeAgo(isoStr) {
        try {
            const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
            if (diff < 60)    return `${Math.floor(diff)}s ago`;
            if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
            return `${Math.floor(diff / 86400)}d ago`;
        } catch { return ''; }
    }

    // ── Results message helper ────────────────────────────────────────────────────

    function _setResultsMessage(html) {
        const el = document.getElementById('scn-results');
        if (el) el.innerHTML = `<div class="list-message">${html}</div>`;
    }

    // ── Scan orchestration ────────────────────────────────────────────────────────

    function _scanProgressMessage() {
        const elapsed = _state.scanStart ? Math.floor((Date.now() - _state.scanStart) / 1000) : 0;
        _setResultsMessage(`<span class="spinner"></span> Scan in progress… ${elapsed}s elapsed`);
    }

    async function runScan() {
        teardown();                       // clear any prior retry timer
        _state.scanStart = Date.now();
        _setResultsMessage('<span class="spinner"></span> Scanning… cold scans take 3–6 min.');
        const done = await _fetchScan();
        if (!done) {
            _state.scanPollId = setInterval(async () => {
                _scanProgressMessage();
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

    function _handleScanResult(data) {
        // Guard: user navigated away mid-scan, shell is gone — bail.
        if (!document.getElementById('scn-funnel')) return;
        _state.lastResult = data;
        _state.candidates = data.candidates || [];
        _renderFunnel(data);
        _state.cspPage = 1;
        _renderCandidates();
        _renderStockPerf(_state.candidates);
    }

    function _renderCandidates() {
        const listEl = document.getElementById('scn-results');
        if (!listEl) return;
        const sorted = [..._state.candidates].sort((a, b) =>
            (parseFloat(b.annualized_roc) || 0) - (parseFloat(a.annualized_roc) || 0));
        const totalPages = Math.max(1, Math.ceil(sorted.length / CSP_PER_PAGE));
        _state.cspPage = Math.min(_state.cspPage, totalPages);
        const slice = sorted.slice((_state.cspPage - 1) * CSP_PER_PAGE, _state.cspPage * CSP_PER_PAGE);
        if (!slice.length) {
            listEl.innerHTML = '<div class="list-message">No CSP candidates found</div>';
            _renderPager(0);
            return;
        }
        listEl.innerHTML = slice.map((c, i) => {
            const roc = parseFloat(c.roc_percent) || 0;
            const yld = c.annualized_roc ? `${parseFloat(c.annualized_roc).toFixed(1)}%y` : '—';
            const tierCls = roc >= 3 ? 'up' : roc >= 1.5 ? 'tier-mid' : '';
            const name = _state.namesBySymbol[c.symbol];
            return `<div class="option-card ${tierCls}" style="--row-delay:${i * 20}ms" onclick="openTradingView('${_escapeHtml(c.symbol)}')">
            <div class="oc-row1">
                <span class="oc-sym-wrap"><span class="oc-symbol">${_escapeHtml(c.symbol)}</span>${name ? `<span class="oc-subname">${_escapeHtml(name)}</span>` : ''}</span>
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

    function _renderPager(totalPages) {
        const el = document.getElementById('scn-pagination');
        if (!el) return;
        if (totalPages <= 1) { el.style.display = 'none'; return; }
        el.style.display = 'flex';
        el.innerHTML = `
        <button class="page-btn" ${_state.cspPage <= 1 ? 'disabled' : ''} onclick="ScannerView._step(-1)">←</button>
        <span class="page-info-text">${_state.cspPage} / ${totalPages}</span>
        <button class="page-btn" ${_state.cspPage >= totalPages ? 'disabled' : ''} onclick="ScannerView._step(1)">→</button>`;
    }

    // Stock Performance: renders the same tickers table as the Watchlist Tickers tab
    // (symbol + company name + sparkline + switchable column groups + sortable headers),
    // reusing window.MITickers (COL_GROUPS + buildSparklineSVG) so columns never drift.
    async function _renderStockPerf(candidates) {
        const host = document.getElementById('scn-stockperf');
        if (!host) return;
        const tickers = [...new Set((candidates || []).map(c => c.symbol))];
        if (!tickers.length) { host.style.display = 'none'; return; }
        try {
            const res = await fetch(`${API_BASE}/screener/stocks?tickers=${tickers.join(',')}`);
            if (!res.ok) { host.style.display = 'none'; return; }
            const data = await res.json();
            const rows = data.candidates || [];
            if (!rows.length) { host.style.display = 'none'; return; }
            _state.perfStocks = rows;
            _state.namesBySymbol = {};
            rows.forEach(s => { _state.namesBySymbol[s.symbol] = s.name; });
            host.style.display = '';
            host.innerHTML = `
          <button class="scn-perf-toggle" onclick="ScannerView._togglePerf()">
            <span>Stock Performance (${rows.length})</span><span class="scn-perf-caret">▾</span>
          </button>
          <div class="scn-perf-body" id="scn-perf-body" style="display:none">
            <div class="col-tabs" id="scn-perf-coltabs"></div>
            <div class="col-header-row" id="scn-perf-colheader"></div>
            <div class="ticker-list" id="scn-perf-list"></div>
          </div>`;
            _renderPerfTable();
            _renderCandidates();   // company names now available — re-render CSP cards with them
        } catch { host.style.display = 'none'; }
    }

    function _perfSorted() {
        const { column, asc } = _state.perfSort;
        return [..._state.perfStocks].sort((a, b) => {
            const vA = a[column], vB = b[column];
            if (vA === 'N/A' || vA == null) return 1;
            if (vB === 'N/A' || vB == null) return -1;
            const nA = parseFloat(vA), nB = parseFloat(vB);
            if (!isNaN(nA) && !isNaN(nB)) return asc ? nA - nB : nB - nA;
            return asc ? String(vA).localeCompare(String(vB)) : String(vB).localeCompare(String(vA));
        });
    }

    function _renderPerfTable() {
        const MIT = window.MITickers;
        const tabsEl = document.getElementById('scn-perf-coltabs');
        const hdrEl = document.getElementById('scn-perf-colheader');
        const listEl = document.getElementById('scn-perf-list');
        if (!MIT || !tabsEl || !hdrEl || !listEl) return;
        const group = MIT.COL_GROUPS[_state.perfColGroup];

        tabsEl.innerHTML = MIT.COL_GROUPS.map((g, i) =>
            `<button class="col-tab${i === _state.perfColGroup ? ' active' : ''}" onclick="ScannerView._perfGroup(${i})">${g.label}</button>`
        ).join('');

        hdrEl.innerHTML = '<div></div><div></div>' + group.cols.map(col => {
            const sorted = _state.perfSort.column === col.key;
            return `<div class="ch-col${sorted ? ' sorted' : ''}" onclick="ScannerView._perfSortBy('${col.key}')">${col.h}${sorted ? ` <span class="sort-dir">${_state.perfSort.asc ? '↑' : '↓'}</span>` : ''}</div>`;
        }).join('');

        listEl.innerHTML = _perfSorted().map((c, i) => {
            const rowDir = c.pct_1d > 0 ? 'up' : c.pct_1d < 0 ? 'down' : '';
            const sym = _escapeHtml(c.symbol);
            const colCells = group.cols.map(col => {
                const cls = col.cls(c);
                const val = col.fmt(c);
                return col.pill
                    ? `<div class="tr-col-wrap"><span class="tr-col pct-pill ${cls}">${val}</span></div>`
                    : `<div class="tr-col ${cls}">${val}</div>`;
            }).join('');
            return `<div class="ticker-row ${rowDir}" style="--row-delay:${i * 25}ms" onclick="openTradingView('${sym}')">
            <div class="tr-left"><span class="tr-symbol">${sym}</span><span class="tr-name">${_escapeHtml(c.name)}</span></div>
            <div class="tr-spark">${MIT.buildSparklineSVG(c.price_history_1m, c.pct_1m >= 0)}</div>
            ${colCells}
        </div>`;
        }).join('');
    }

    function _renderFunnel(data) {
        const el = document.getElementById('scn-funnel');
        if (!el) return;
        const s = data.filter_summary || {};
        const cache = data.cached
            ? `cached · ${data.cached_at ? _timeAgo(data.cached_at) : ''}`
            : `live · ${data.market_status || ''}`;
        const cell = (label, val) => `<div class="fc"><div class="fc-label">${label}</div><div class="fc-val">${val ?? '—'}</div></div>`;
        el.style.display = '';
        el.innerHTML =
            cell('Universe', s.combined_unique) +
            cell('Fundamental', s.fundamental_passed) +
            cell('Vol', s.vol_passed) +
            cell('Technical', s.technical_passed) +
            cell('Candidates', s.options_screener_returned) +
            `<div class="fc fc-cache">${_escapeHtml(cache)}</div>`;
    }

    // ── Filter sheet HTML builder ─────────────────────────────────────────────────

    function _sheetHtml() {
        const p = _state.params;

        // Universe inputs (PARAM_CONFIG[0..9]) — no scaling
        const universeFields = PARAM_CONFIG.slice(0, 10).map(cfg => {
            const val = p[cfg.key] != null ? p[cfg.key] : '';
            return `<div class="scn-field">
                <label class="scn-field-label">${_escapeHtml(cfg.label)}${cfg.suffix ? ' (' + _escapeHtml(cfg.suffix) + ')' : ''}</label>
                <input class="scn-field-input" type="number" id="scnf-${_escapeHtml(cfg.key)}"
                    value="${_escapeHtml(String(val))}"
                    min="${cfg.min}" max="${cfg.max}" step="${cfg.step}">
            </div>`;
        }).join('');

        // Fundamentals inputs (PARAM_CONFIG[10..14]) — apply scale for display
        const fundFields = PARAM_CONFIG.slice(10).map(cfg => {
            const raw = p[cfg.key];
            const val = raw == null ? '' : raw * (cfg.scale || 1);
            return `<div class="scn-field">
                <label class="scn-field-label">${_escapeHtml(cfg.label)}${cfg.suffix ? ' (' + _escapeHtml(cfg.suffix) + ')' : ''}</label>
                <input class="scn-field-input" type="number" id="scnf-${_escapeHtml(cfg.key)}"
                    value="${val === '' ? '' : _escapeHtml(String(val))}"
                    min="${cfg.min}" max="${cfg.max}" step="${cfg.step}"
                    placeholder="any">
            </div>`;
        }).join('');

        // Condition chips
        const condChips = _state.availableConditions.map(cond => {
            const active = p.conditions.includes(cond.id) ? ' active' : '';
            return `<button class="scn-cond-chip${active}" data-cond-id="${_escapeHtml(cond.id)}"
                title="${_escapeHtml(cond.description || '')}">${_escapeHtml(cond.label)}</button>`;
        }).join('');

        // Sector checkboxes
        const sectorChecks = _state.availableSectors.map(sector => {
            const checked = p.sectors.includes(sector) ? ' checked' : '';
            return `<label class="scn-sector-check">
                <input type="checkbox" value="${_escapeHtml(sector)}"${checked}>
                <span>${_escapeHtml(sector)}</span>
            </label>`;
        }).join('');

        const restrictChecked = p.restrict_to_watchlist_universe ? ' checked' : '';

        return `<div class="scn-sheet">
            <div class="scn-sheet-header">
                <span class="scn-sheet-title">Scan Filters</span>
                <button class="scn-sheet-close" onclick="ScannerView.closeFilterSheet()">✕</button>
            </div>
            <div class="scn-sheet-body">
                <div class="scn-sheet-section">
                    <div class="scn-sheet-section-title">Universe</div>
                    ${universeFields}
                </div>
                <div class="scn-sheet-section">
                    <div class="scn-sheet-section-title">Fundamentals</div>
                    ${fundFields}
                </div>
                <div class="scn-sheet-section">
                    <div class="scn-sheet-section-title">Technical Conditions</div>
                    <div class="scn-cond-chips">${condChips || '<span style="color:var(--tv-muted);font-size: 13px">None available</span>'}</div>
                </div>
                <div class="scn-sheet-section">
                    <div class="scn-sheet-section-title">Sectors</div>
                    <div class="scn-sector-checks">${sectorChecks || '<span style="color:var(--tv-muted);font-size: 13px">None available</span>'}</div>
                </div>
                <div class="scn-sheet-section">
                    <div class="scn-sheet-section-title">Universe Scope</div>
                    <label class="scn-sector-check">
                        <input type="checkbox" id="scnf-restrict"${restrictChecked}>
                        <span>Restrict to S&amp;P 500 + NASDAQ 100</span>
                    </label>
                </div>
            </div>
            <div class="scn-sheet-footer">
                <button class="scn-sheet-apply" onclick="ScannerView.applyAndScan()">Apply &amp; Scan</button>
                <button class="scn-sheet-rescan" onclick="ScannerView.forceRescan()">Force Rescan</button>
            </div>
        </div>`;
    }

    // ── Sheet open / close ────────────────────────────────────────────────────────

    function openFilterSheet() {
        const root = document.getElementById('scn-sheet-root');
        if (!root) return;
        root.innerHTML = _sheetHtml();
        // Wire chip click handlers
        document.querySelectorAll('#scn-sheet-root .scn-cond-chip').forEach(chip => {
            chip.addEventListener('click', () => chip.classList.toggle('active'));
        });
        root.classList.add('open');
    }

    function closeFilterSheet() {
        const root = document.getElementById('scn-sheet-root');
        if (!root) return;
        root.classList.remove('open');
        root.innerHTML = '';
    }

    // ── Read sheet inputs back into _state.params ─────────────────────────────────

    function _readSheetIntoParams() {
        const p = _state.params;

        // Universe params (indices 0–9): no scaling
        PARAM_CONFIG.slice(0, 10).forEach(cfg => {
            const input = document.getElementById(`scnf-${cfg.key}`);
            if (!input) return;
            const parsed = parseFloat(input.value);
            p[cfg.key] = isNaN(parsed) ? DEFAULT_PARAMS[cfg.key] : parsed;
        });

        // Fundamental params (indices 10–14): apply inverse scale; nullable
        PARAM_CONFIG.slice(10).forEach(cfg => {
            const input = document.getElementById(`scnf-${cfg.key}`);
            if (!input) return;
            if (input.value === '' && cfg.nullable) {
                p[cfg.key] = null;
            } else {
                const parsed = parseFloat(input.value);
                if (!isNaN(parsed)) p[cfg.key] = parsed / (cfg.scale || 1);
            }
        });

        // Condition chips
        p.conditions = Array.from(
            document.querySelectorAll('#scn-sheet-root .scn-cond-chip.active')
        ).map(chip => chip.dataset.condId);

        // Sector checkboxes
        p.sectors = Array.from(
            document.querySelectorAll('#scn-sheet-root .scn-sector-checks input[type="checkbox"]:checked')
        ).map(cb => cb.value);

        // Universe scope toggle
        const restrictEl = document.getElementById('scnf-restrict');
        if (restrictEl) p.restrict_to_watchlist_universe = restrictEl.checked;
    }

    // ── Apply & Scan ──────────────────────────────────────────────────────────────

    function applyAndScan() {
        _readSheetIntoParams();
        _persistParams();
        closeFilterSheet();
        _renderHeader();
        _renderActiveParams();
        runScan();
    }

    // ── Force Rescan (port from v1 scanner.js) ────────────────────────────────────

    async function forceRescan() {
        teardown();   // cancel any in-flight runScan poll before restarting
        _readSheetIntoParams();
        _persistParams();
        closeFilterSheet();
        _renderHeader();
        _renderActiveParams();

        // Auto-refresh market data if it's more than 18 hours old
        try {
            const statusRes = await fetch(`${API_BASE}/market-data/status`);
            if (statusRes.ok) {
                const status = await statusRes.json();
                if (status.stale_hours != null && status.stale_hours > 18) {
                    _setResultsMessage(`<span class="spinner"></span> Data is ${Math.round(status.stale_hours)}h old — refreshing before scan…`);
                    await _refreshMarketData();
                }
            }
        } catch (e) {
            // Network error checking status — proceed with scan anyway
        }

        _setResultsMessage('<span class="spinner"></span> Clearing cache…');
        try {
            await fetch(`${API_BASE}/screener/csp-scan?${_buildQueryString()}`, { method: 'DELETE' });
        } catch (err) {
            _setResultsMessage(`Failed to clear cache: ${_escapeHtml(err.message)}`);
            return;
        }

        _state.scanStart = Date.now();
        _setResultsMessage('<span class="spinner"></span> Cache cleared. Running fresh scan…');
        const done = await _fetchScan();
        if (!done) {
            _state.scanPollId = setInterval(async () => {
                _scanProgressMessage();
                if (await _fetchScan()) { teardown(); }
            }, 6000);
        }
    }

    // ── Refresh market data (port from v1 scanner.js) ─────────────────────────────
    // Triggers a market data refresh and waits up to 6 minutes for it to complete.
    // Returns true if data became fresh, false on timeout or error.

    async function _refreshMarketData() {
        try {
            const res = await fetch(`${API_BASE}/market-data/refresh`, { method: 'POST' });
            if (!res.ok) return false;
        } catch (e) {
            return false;
        }

        const startTime = Date.now();
        const maxWaitMs = 6 * 60 * 1000;

        while (Date.now() - startTime < maxWaitMs) {
            await new Promise(r => setTimeout(r, 20000));
            const elapsed = Math.round((Date.now() - startTime) / 1000);
            _setResultsMessage(`<span class="spinner"></span> Refreshing market data… ${elapsed}s elapsed`);
            try {
                const statusRes = await fetch(`${API_BASE}/market-data/status`);
                if (statusRes.ok) {
                    const status = await statusRes.json();
                    if (!status.is_stale) {
                        await loadDataFreshness();
                        return true;
                    }
                }
            } catch (e) {
                // transient error — keep polling
            }
        }
        return false;
    }

    // ── View lifecycle ────────────────────────────────────────────────────────────

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
        document.getElementById('scn-filters-btn').addEventListener('click', openFilterSheet);
        Promise.all([loadAvailableConditions(), _loadSectors(), loadDataFreshness()])
            .then(() => { _renderHeader(); _renderActiveParams(); runScan(); });
    }

    function teardown() {
        if (_state.scanPollId) { clearInterval(_state.scanPollId); _state.scanPollId = null; }
    }

    window.ScannerView = {
        render, teardown,
        _step: (d) => { _state.cspPage += d; _renderCandidates(); },
        _togglePerf: () => {
            const body = document.getElementById('scn-perf-body');
            const caret = document.querySelector('.scn-perf-caret');
            if (!body) return;
            const open = body.style.display === 'none';
            body.style.display = open ? '' : 'none';
            if (caret) caret.textContent = open ? '▴' : '▾';
        },
        _perfGroup: (i) => {
            _state.perfColGroup = i;
            _state.perfSort = { column: window.MITickers.COL_GROUPS[i].cols[0].key, asc: false };
            _renderPerfTable();
        },
        _perfSortBy: (key) => {
            _state.perfSort.asc = _state.perfSort.column === key ? !_state.perfSort.asc : false;
            _state.perfSort.column = key;
            _renderPerfTable();
        },
        openFilterSheet, closeFilterSheet, applyAndScan, forceRescan,
    };

})();
