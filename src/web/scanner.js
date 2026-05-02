/**
 * scanner.js — CSP Universe Scanner dashboard page
 *
 * Communicates with:
 *   GET  /api/screener/csp-scan/conditions  — available technical conditions
 *   GET  /api/screener/csp-scan?<params>    — run scan with current params
 *   DELETE /api/screener/csp-scan?<params>  — bust cache for current params
 */

'use strict';

const API_BASE = (() => {
    if (window.MARKET_INTELLIGENCE_CONFIG && window.MARKET_INTELLIGENCE_CONFIG.apiBase) {
        return window.MARKET_INTELLIGENCE_CONFIG.apiBase.replace(/\/$/, '');
    }
    return '';
})();

// ── Scanner state ─────────────────────────────────────────────────────────────

const _state = {
    // Current param values — editable by user
    params: {
        min_cap:   10,
        max_price: 150,
        min_beta:  0.8,
        max_beta:  2.4,
        min_vol:   30,
        rsi_max:   50,
        dte_min:   3,
        dte_max:   46,
        conditions: [],   // list of active condition IDs
    },
    // Available conditions fetched from API
    availableConditions: [],
    // Results
    results: [],
    sortKey: 'composite_score',
    sortAsc: false,
    // UI
    scanPollId: null,
    scanStart:  null,
    conditionsOpen: false,
    groupByTicker: false,
};

// ── Param config — drives badge rendering ─────────────────────────────────────

const PARAM_CONFIG = [
    { key: 'min_cap',   label: 'Cap >',      suffix: 'B',  min: 1,   max: 500,  step: 1,   decimals: 0 },
    { key: 'max_price', label: 'Price <',    suffix: '$',  min: 10,  max: 500,  step: 5,   decimals: 0, prefix: '$' },
    { key: 'min_beta',  label: 'Beta min',   suffix: '',   min: 0.1, max: 3.0,  step: 0.1, decimals: 1 },
    { key: 'max_beta',  label: 'Beta max',   suffix: '',   min: 0.5, max: 5.0,  step: 0.1, decimals: 1 },
    { key: 'min_vol',   label: 'Vol ≥',      suffix: '%',  min: 10,  max: 100,  step: 5,   decimals: 0 },
    { key: 'rsi_max',   label: 'RSI <',      suffix: '',   min: 10,  max: 100,  step: 1,   decimals: 0 },
    { key: 'dte_min',   label: 'DTE ≥',      suffix: 'd',  min: 1,   max: 90,   step: 1,   decimals: 0 },
    { key: 'dte_max',   label: 'DTE ≤',      suffix: 'd',  min: 1,   max: 180,  step: 1,   decimals: 0 },
];

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    renderParamBadges();
    loadAvailableConditions().then(() => renderConditionPicker());
    loadDataFreshness();
});

// ── Load available conditions from API ────────────────────────────────────────

async function loadAvailableConditions() {
    try {
        const res = await fetch(`${API_BASE}/screener/csp-scan/conditions`);
        if (!res.ok) return;
        const data = await res.json();
        _state.availableConditions = data.conditions || [];
    } catch (e) {
        console.warn('Could not load conditions:', e);
    }
}

// ── Param badges ──────────────────────────────────────────────────────────────

function renderParamBadges() {
    const container = document.getElementById('param-badges');
    if (!container) return;

    container.innerHTML = PARAM_CONFIG.map(cfg => {
        const val = _state.params[cfg.key];
        const display = cfg.prefix
            ? `${cfg.prefix}${val.toFixed(cfg.decimals)}`
            : `${val.toFixed(cfg.decimals)}${cfg.suffix}`;
        return `
            <div class="param-badge" id="badge-${cfg.key}" onclick="openParamEdit('${cfg.key}')">
                <span class="pb-label">${cfg.label}</span>
                <span class="pb-value" id="pbv-${cfg.key}">${display}</span>
                <span class="pb-edit-icon">✎</span>
            </div>`;
    }).join('');
}

function openParamEdit(key) {
    // Close any other open editor first
    closeAllParamEdits();

    const cfg = PARAM_CONFIG.find(c => c.key === key);
    if (!cfg) return;

    const badge = document.getElementById(`badge-${key}`);
    if (!badge) return;

    badge.classList.add('editing');
    badge.innerHTML = `
        <span class="pb-label">${cfg.label}</span>
        <input
            id="param-input-${key}"
            class="pb-input"
            type="number"
            value="${_state.params[key]}"
            min="${cfg.min}"
            max="${cfg.max}"
            step="${cfg.step}"
            onkeydown="handleParamKey(event, '${key}')"
            onblur="commitParamEdit('${key}')"
        >
        <span class="pb-suffix">${cfg.suffix}</span>
    `;

    const input = document.getElementById(`param-input-${key}`);
    if (input) {
        input.focus();
        input.select();
    }
}

function handleParamKey(event, key) {
    if (event.key === 'Enter')  { event.target.blur(); }
    if (event.key === 'Escape') { closeAllParamEdits(); renderParamBadges(); }
}

function commitParamEdit(key) {
    const input = document.getElementById(`param-input-${key}`);
    if (!input) return;

    const cfg = PARAM_CONFIG.find(c => c.key === key);
    let val = parseFloat(input.value);

    // Clamp to allowed range
    if (isNaN(val)) val = _state.params[key];
    val = Math.max(cfg.min, Math.min(cfg.max, val));
    _state.params[key] = val;

    renderParamBadges();
}

function closeAllParamEdits() {
    PARAM_CONFIG.forEach(cfg => {
        const badge = document.getElementById(`badge-${cfg.key}`);
        if (badge) badge.classList.remove('editing');
    });
}

// ── Condition picker ──────────────────────────────────────────────────────────

function renderConditionPicker() {
    const pickerBody = document.getElementById('condition-picker-body');
    if (!pickerBody || !_state.availableConditions.length) return;

    // Group by group field
    const groups = {};
    _state.availableConditions.forEach(c => {
        if (!groups[c.group]) groups[c.group] = [];
        groups[c.group].push(c);
    });

    pickerBody.innerHTML = Object.entries(groups).map(([group, conds]) => `
        <div class="cp-group">
            <div class="cp-group-label">${group}</div>
            ${conds.map(c => {
                const active = _state.params.conditions.includes(c.id);
                return `
                    <label class="cp-item ${active ? 'active' : ''}" id="cp-item-${c.id}">
                        <input
                            type="checkbox"
                            ${active ? 'checked' : ''}
                            onchange="toggleCondition('${c.id}', this.checked)"
                        >
                        <span class="cp-item-label">${c.label}</span>
                        <span class="cp-item-desc">${c.description}</span>
                    </label>`;
            }).join('')}
        </div>
    `).join('');

    renderConditionChips();
}

function toggleCondition(id, checked) {
    if (checked && !_state.params.conditions.includes(id)) {
        _state.params.conditions.push(id);
    } else if (!checked) {
        _state.params.conditions = _state.params.conditions.filter(c => c !== id);
    }
    // Update the item styling
    const item = document.getElementById(`cp-item-${id}`);
    if (item) item.classList.toggle('active', checked);
    renderConditionChips();
}

function removeCondition(id) {
    _state.params.conditions = _state.params.conditions.filter(c => c !== id);
    // Uncheck in picker
    const item = document.getElementById(`cp-item-${id}`);
    if (item) {
        item.classList.remove('active');
        const cb = item.querySelector('input[type=checkbox]');
        if (cb) cb.checked = false;
    }
    renderConditionChips();
}

function renderConditionChips() {
    const container = document.getElementById('condition-chips');
    if (!container) return;

    if (!_state.params.conditions.length) {
        container.innerHTML = '<span class="no-conditions">No conditions selected — all vol-passing tickers proceed to options screener</span>';
        return;
    }

    container.innerHTML = _state.params.conditions.map(id => {
        const cond = _state.availableConditions.find(c => c.id === id);
        const label = cond ? cond.label : id;
        return `<span class="condition-chip">
            ${_esc(label)}
            <button class="chip-remove" onclick="removeCondition('${id}')" title="Remove">×</button>
        </span>`;
    }).join('');
}

function toggleConditionPicker() {
    _state.conditionsOpen = !_state.conditionsOpen;
    const panel = document.getElementById('condition-picker-panel');
    const btn   = document.getElementById('btn-conditions');
    if (panel) panel.style.display = _state.conditionsOpen ? 'block' : 'none';
    if (btn)   btn.textContent = _state.conditionsOpen ? 'Close ▲' : 'Add Conditions ▼';
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
        min_dte:   p.dte_min,
        max_dte:   p.dte_max,
    });
    if (p.conditions.length) {
        qs.set('conditions', p.conditions.join(','));
    }
    return qs.toString();
}

// ── Scan ──────────────────────────────────────────────────────────────────────

async function startScan() {
    closeAllParamEdits();
    _disableButtons();
    _state.scanStart = Date.now();
    setStatus('running', '<span class="spinner"></span> Scanning S&P 500 + NASDAQ 100 universe… This may take 3–6 minutes on a cold cache.');

    if (_state.scanPollId) { clearInterval(_state.scanPollId); _state.scanPollId = null; }

    const done = await _fetchScan();
    if (!done) {
        _state.scanPollId = setInterval(async () => {
            const elapsed = Math.floor((Date.now() - _state.scanStart) / 1000);
            setStatus('running', `<span class="spinner"></span> Scan in progress… ${elapsed}s elapsed`);
            const finished = await _fetchScan();
            if (finished) { clearInterval(_state.scanPollId); _state.scanPollId = null; }
        }, 6000);
    }
}

async function forceRescan() {
    closeAllParamEdits();
    _disableButtons();
    setStatus('running', '<span class="spinner"></span> Clearing cache…');
    try {
        await fetch(`${API_BASE}/screener/csp-scan?${_buildQueryString()}`, { method: 'DELETE' });
    } catch (err) {
        setStatus('error', `Failed to clear cache: ${err.message}`);
        _enableButtons();
        return;
    }
    _state.scanStart = Date.now();
    setStatus('running', '<span class="spinner"></span> Cache cleared. Running fresh scan…');
    const done = await _fetchScan();
    if (!done) {
        _state.scanPollId = setInterval(async () => {
            const elapsed = Math.floor((Date.now() - _state.scanStart) / 1000);
            setStatus('running', `<span class="spinner"></span> Scan in progress… ${elapsed}s elapsed`);
            const finished = await _fetchScan();
            if (finished) { clearInterval(_state.scanPollId); _state.scanPollId = null; }
        }, 6000);
    }
}

async function _fetchScan() {
    try {
        const res = await fetch(`${API_BASE}/screener/csp-scan?${_buildQueryString()}`);
        if (!res.ok) {
            setStatus('error', `Server error: ${res.status} ${res.statusText}`);
            _enableButtons();
            return false;
        }
        const data = await res.json();
        _handleScanResult(data);
        return true;
    } catch (err) {
        setStatus('error', `Network error: ${err.message}`);
        _enableButtons();
        return false;
    }
}

// ── Handle result ─────────────────────────────────────────────────────────────

function _handleScanResult(data) {
    _enableButtons();
    document.getElementById('btn-rescan').style.display = '';

    const summary    = data.filter_summary || {};
    const candidates = data.candidates || [];

    // Funnel strip
    document.getElementById('fc-universe').textContent    = _fmt(summary.combined_unique);
    document.getElementById('fc-fundamental').textContent = _fmt(summary.fundamental_passed);
    document.getElementById('fc-vol').textContent         = _fmt(summary.vol_passed);
    document.getElementById('fc-technical').textContent   = _fmt(summary.technical_passed);
    document.getElementById('fc-candidates').textContent  = _fmt(summary.options_screener_returned);

    if (data.cached) {
        document.getElementById('fc-cache-status').textContent = 'Cached';
        document.getElementById('fc-cache-at').textContent     = data.cached_at ? `Updated ${_timeAgo(data.cached_at)}` : '';
    } else {
        document.getElementById('fc-cache-status').textContent = 'Live';
        document.getElementById('fc-cache-at').textContent     = data.market_status || '';
    }

    document.getElementById('funnel-strip').style.display  = '';
    document.getElementById('results-section').style.display = '';

    const elapsed = _state.scanStart ? `in ${((Date.now() - _state.scanStart) / 1000).toFixed(0)}s` : '';
    setStatus(
        data.cached ? 'done' : 'done',
        data.cached
            ? `✓ Showing cached results (${data.market_status || ''})`
            : `✓ Scan complete ${elapsed} — ${candidates.length} candidate${candidates.length !== 1 ? 's' : ''} found`
    );

    document.getElementById('cache-badge-scan').textContent =
        `${data.cached ? 'cached' : 'live'} · ${data.market_status || ''}`;

    _state.results = candidates;
    _state.sortKey  = 'composite_score';
    _state.sortAsc  = false;
    renderResults();
}

// ── Render results ────────────────────────────────────────────────────────────

function _bestPerTicker(rows) {
    const best = new Map();
    for (const c of rows) {
        const prev = best.get(c.symbol);
        if (!prev || (c.annualized_roc ?? -Infinity) > (prev.annualized_roc ?? -Infinity)) {
            best.set(c.symbol, c);
        }
    }
    return [...best.values()];
}

function toggleGroupByTicker(on) {
    _state.groupByTicker = on;
    renderResults();
}

function renderResults() {
    const container = document.getElementById('results-list');
    if (!_state.results || !_state.results.length) {
        container.innerHTML = '<div class="state-msg">No CSP candidates found for this scan run.</div>';
        return;
    }

    const display = _state.groupByTicker ? _bestPerTicker(_state.results) : _state.results;

    document.getElementById('results-count').textContent = _state.groupByTicker
        ? `${display.length} of ${_state.results.length} results`
        : `${_state.results.length} results`;

    const sorted = [...display].sort((a, b) => {
        const va = _numOrStr(a[_state.sortKey]);
        const vb = _numOrStr(b[_state.sortKey]);
        if (typeof va === 'number' && typeof vb === 'number') {
            return _state.sortAsc ? va - vb : vb - va;
        }
        return _state.sortAsc
            ? String(va).localeCompare(String(vb))
            : String(vb).localeCompare(String(va));
    });

    container.innerHTML = sorted.map(c => {
        const score    = typeof c.composite_score === 'number' ? c.composite_score : 0;
        const scoreCls = score >= 60 ? 'score-high' : score >= 40 ? 'score-mid' : 'score-low';
        const ivCls    = c.impliedVolatility >= 40 ? 'positive' : c.impliedVolatility >= 25 ? 'neutral' : 'negative';
        const rocCls   = c.annualized_roc >= 20 ? 'positive' : c.annualized_roc >= 10 ? 'neutral' : 'negative';
        const ratio    = c.iv_rv_ratio;
        const ratioCls = ratio == null ? 'neutral' : ratio >= 1.2 ? 'positive' : ratio >= 0.8 ? 'neutral' : 'negative';

        return `<div class="scan-row">
            <div class="symbol">${_esc(c.symbol)}</div>
            <div>${_esc(c.expiration || '—')}</div>
            <div>$${_fmt2(c.strike)}</div>
            <div>$${_fmt2(c.premium)}</div>
            <div>${_fmt0(c.dte)}d</div>
            <div>${_fmt1(c.otm_percent)}%</div>
            <div class="${rocCls}">${_fmt1(c.annualized_roc)}%</div>
            <div class="${ivCls}">${_fmt1(c.impliedVolatility)}%</div>
            <div class="neutral">${_fmt1(c.rv20)}%</div>
            <div class="${ratioCls}">${ratio != null ? ratio.toFixed(2) : '—'}</div>
            <div><span class="score-pill ${scoreCls}">${score.toFixed(1)}</span></div>
        </div>`;
    }).join('');
}

function sortResults(key) {
    if (_state.sortKey === key) { _state.sortAsc = !_state.sortAsc; }
    else { _state.sortKey = key; _state.sortAsc = false; }
    renderResults();
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function _disableButtons() {
    document.getElementById('btn-scan').disabled   = true;
    document.getElementById('btn-rescan').disabled = true;
}
function _enableButtons() {
    document.getElementById('btn-scan').disabled   = false;
    document.getElementById('btn-rescan').disabled = false;
}

function setStatus(type, html) {
    const el = document.getElementById('scan-status');
    el.className = `scan-status ${type}`;
    el.innerHTML = html;
}

function _numOrStr(v) {
    if (v === null || v === undefined || v === 'N/A') return -Infinity;
    const n = parseFloat(v);
    return isNaN(n) ? String(v) : n;
}

function _fmt(n)  { return n != null ? Number(n).toLocaleString() : '—'; }
function _fmt0(n) { return n != null && n !== 'N/A' ? Number(n).toFixed(0) : '—'; }
function _fmt1(n) { return n != null && n !== 'N/A' ? Number(n).toFixed(1) : '—'; }
function _fmt2(n) { return n != null && n !== 'N/A' ? Number(n).toFixed(2) : '—'; }

function _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _timeAgo(isoStr) {
    try {
        const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
        if (diff < 60)    return `${Math.floor(diff)}s ago`;
        if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    } catch { return ''; }
}

// ── Data freshness badge ────────────────────────────────────────────────────────

async function loadDataFreshness() {
    const badge = document.getElementById('data-freshness');
    const text  = document.getElementById('data-freshness-text');
    const warn  = document.getElementById('stale-warning');
    const staleHoursEl = document.getElementById('stale-hours');

    if (!badge || !text) return;

    try {
        const res = await fetch(`${API_BASE}/market-data/status`);
        if (!res.ok) {
            text.textContent = 'Status unavailable';
            return;
        }
        const data = await res.json();

        if (data.fundamentals_count === 0 && data.ohlcv_ticker_count === 0) {
            badge.className = 'data-freshness-badge empty';
            text.textContent = 'No local data — will use live yfinance';
            return;
        }

        const tickers = data.ohlcv_ticker_count || 0;
        const latest  = data.ohlcv_latest_date || 'unknown';

        if (data.is_stale) {
            badge.className = 'data-freshness-badge stale';
            const hours = data.stale_hours != null ? Math.round(data.stale_hours) : '?';
            text.textContent = `Data: ${tickers} tickers · ${hours}h old (stale)`;
            if (warn && staleHoursEl) {
                staleHoursEl.textContent = hours;
                warn.style.display = 'block';
            }
        } else {
            badge.className = 'data-freshness-badge';
            const updatedAt = data.fundamentals_updated_at;
            const ago = updatedAt ? _timeAgo(updatedAt) : 'recently';
            text.textContent = `Data: ${tickers} tickers · updated ${ago}`;
            if (warn) warn.style.display = 'none';
        }
    } catch (e) {
        console.warn('Could not load data freshness:', e);
        text.textContent = 'Status unavailable';
    }
}

async function triggerDataRefresh() {
    const warn = document.getElementById('stale-warning');
    const text = document.getElementById('data-freshness-text');

    try {
        const res = await fetch(`${API_BASE}/market-data/refresh`, { method: 'POST' });
        if (res.ok) {
            if (warn) warn.innerHTML = '⏳ Data refresh started in the background. This takes ~2–3 minutes. Reload the page to check progress.';
            if (text) text.textContent = 'Refreshing…';
        } else {
            if (warn) warn.innerHTML = '❌ Refresh failed. Check server logs.';
        }
    } catch (e) {
        console.error('Data refresh trigger failed:', e);
        if (warn) warn.innerHTML = '❌ Network error triggering refresh.';
    }
}
