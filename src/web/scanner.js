/**
 * scanner.js — CSP Universe Scanner dashboard page
 *
 * Communicates with GET /api/screener/csp-scan
 *
 * The scan is expensive (~3–6 min on a cache miss) so it is NOT auto-triggered
 * on page load. The user presses "Run Scan". While waiting, the page polls the
 * endpoint every 6 seconds so it picks up the result as soon as it's ready.
 *
 * On a cache hit the response is immediate — the UI updates normally.
 */

'use strict';

// ── API base URL (injected at container startup via config.js) ──────────────
const API_BASE = (() => {
    if (window.MARKET_INTELLIGENCE_CONFIG && window.MARKET_INTELLIGENCE_CONFIG.apiBase) {
        return window.MARKET_INTELLIGENCE_CONFIG.apiBase.replace(/\/$/, '');
    }
    return '';
})();

// ── State ────────────────────────────────────────────────────────────────────
let _results      = [];   // current candidate list
let _sortKey      = 'composite_score';
let _sortAsc      = false;
let _scanPollId   = null; // setInterval handle while waiting for scan
let _scanStart    = null; // Date when Run Scan was pressed

// ── Entry points ─────────────────────────────────────────────────────────────

/**
 * Called by the "Run Scan" button.
 * Immediately hits the endpoint (which may return instantly on cache hit).
 * If the server is still running the scan (unlikely — FastAPI awaits the full
 * result before responding), the status text keeps the user informed.
 */
async function startScan() {
    _disableButtons();
    _scanStart = Date.now();
    setStatus('running', '<span class="spinner"></span> Scanning S&P 500 + NASDAQ 100 universe…  This may take 3–6 minutes on a cold cache.');

    if (_scanPollId) { clearInterval(_scanPollId); _scanPollId = null; }

    const done = await _fetchScan();
    if (!done) {
        _scanPollId = setInterval(async () => {
            const elapsed = Math.floor((Date.now() - _scanStart) / 1000);
            setStatus('running', `<span class="spinner"></span> Scan in progress… ${elapsed}s elapsed`);
            const finished = await _fetchScan();
            if (finished) { clearInterval(_scanPollId); _scanPollId = null; }
        }, 6000);
    }
}

async function forceRescan() {
    _disableButtons();
    setStatus('running', '<span class="spinner"></span> Clearing cache…');
    try {
        await fetch(`${API_BASE}/screener/csp-scan`, { method: 'DELETE' });
    } catch (err) {
        setStatus('error', `Failed to clear cache: ${err.message}`);
        _enableButtons();
        return;
    }
    // Cache is now clear — kick off a fresh scan
    _scanStart = Date.now();
    setStatus('running', '<span class="spinner"></span> Cache cleared. Running fresh scan…');
    const done = await _fetchScan();
    if (!done) {
        _scanPollId = setInterval(async () => {
            const elapsed = Math.floor((Date.now() - _scanStart) / 1000);
            setStatus('running', `<span class="spinner"></span> Scan in progress… ${elapsed}s elapsed`);
            const finished = await _fetchScan();
            if (finished) { clearInterval(_scanPollId); _scanPollId = null; }
        }, 6000);
    }
}

function _disableButtons() {
    document.getElementById('btn-scan').disabled = true;
    document.getElementById('btn-rescan').disabled = true;
}

function _enableButtons() {
    document.getElementById('btn-scan').disabled = false;
    document.getElementById('btn-rescan').disabled = false;
}

/**
 * Hit /api/screener/csp-scan. Returns true when we received a valid payload,
 * false on network error or if the scan appears not yet complete.
 */
async function _fetchScan() {
    try {
        const res = await fetch(`${API_BASE}/screener/csp-scan`);
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
    // Show Force Rescan button now that we have a result
    document.getElementById('btn-rescan').style.display = '';

    const summary = data.filter_summary || {};
    const candidates = data.candidates || [];

    // ── Funnel strip ──────────────────────────────────────────────────────
    document.getElementById('fc-universe').textContent     = _fmt(summary.combined_unique);
    document.getElementById('fc-fundamental').textContent  = _fmt(summary.fundamental_passed);
    document.getElementById('fc-rv').textContent           = _fmt(summary.vol_passed);
    document.getElementById('fc-candidates').textContent   = _fmt(summary.options_screener_returned);

    if (data.cached) {
        document.getElementById('fc-cache-status').textContent = 'Cached';
        const ago = data.cached_at ? _timeAgo(data.cached_at) : '';
        document.getElementById('fc-cache-at').textContent     = ago ? `Updated ${ago}` : data.market_status || '';
    } else {
        document.getElementById('fc-cache-status').textContent = 'Live';
        document.getElementById('fc-cache-at').textContent     = data.market_status || '';
    }

    document.getElementById('funnel-strip').style.display = '';
    document.getElementById('results-section').style.display = '';

    // ── Status text ───────────────────────────────────────────────────────
    const elapsed = _scanStart ? `in ${((Date.now() - _scanStart) / 1000).toFixed(0)}s` : '';
    if (data.cached) {
        setStatus('done', `✓ Showing cached results (${data.market_status || ''})`);
    } else {
        setStatus('done', `✓ Scan complete ${elapsed} — ${candidates.length} candidate${candidates.length !== 1 ? 's' : ''} found`);
    }

    // ── Cache badge ───────────────────────────────────────────────────────
    const cacheBadge = document.getElementById('cache-badge-scan');
    cacheBadge.textContent = data.cached
        ? `cached · ${data.market_status || ''}`
        : `live · ${data.market_status || ''}`;

    // ── Count badge ───────────────────────────────────────────────────────
    document.getElementById('results-count').textContent = `${candidates.length} results`;

    // ── Store and render ──────────────────────────────────────────────────
    _results  = candidates;
    _sortKey  = 'composite_score';
    _sortAsc  = false;
    renderResults();
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderResults() {
    const container = document.getElementById('results-list');

    if (!_results || _results.length === 0) {
        container.innerHTML = '<div class="state-msg">No CSP candidates found for this scan run.</div>';
        return;
    }

    // Sort
    const sorted = [..._results].sort((a, b) => {
        const va = _numOrStr(a[_sortKey]);
        const vb = _numOrStr(b[_sortKey]);
        if (typeof va === 'number' && typeof vb === 'number') {
            return _sortAsc ? va - vb : vb - va;
        }
        return _sortAsc
            ? String(va).localeCompare(String(vb))
            : String(vb).localeCompare(String(va));
    });

    // Build HTML in one pass — avoid innerHTML += in a loop
    const rows = sorted.map(c => {
        const score     = typeof c.composite_score === 'number' ? c.composite_score : 0;
        const scoreCls  = score >= 60 ? 'score-high' : score >= 40 ? 'score-mid' : 'score-low';
        const ivCls     = c.impliedVolatility >= 40 ? 'positive' : c.impliedVolatility >= 25 ? 'neutral' : 'negative';
        const rocCls    = c.annualized_roc >= 20 ? 'positive' : c.annualized_roc >= 10 ? 'neutral' : 'negative';

        // IV/RV ratio: >1.2 = elevated premium (green), 0.8–1.2 = fair (neutral), <0.8 = low premium (red)
        const ratio    = c.iv_rv_ratio;
        const ratioCls = ratio == null ? 'neutral' : ratio >= 1.2 ? 'positive' : ratio >= 0.8 ? 'neutral' : 'negative';
        const ratioStr = ratio != null ? ratio.toFixed(2) : '—';

        return `<div class="scan-row">
            <div class="symbol">${_esc(c.symbol)}</div>
            <div>${_esc(c.expiration || '—')}</div>
            <div>${_fmt2(c.strike)}</div>
            <div>${_fmt2(c.premium)}</div>
            <div>${_fmt0(c.dte)}d</div>
            <div>${_fmt1(c.otm_percent)}%</div>
            <div class="${rocCls}">${_fmt1(c.annualized_roc)}%</div>
            <div class="${ivCls}">${_fmt1(c.impliedVolatility)}%</div>
            <div class="neutral">${_fmt1(c.rv20)}%</div>
            <div class="${ratioCls}">${ratioStr}</div>
            <div><span class="score-pill ${scoreCls}">${score.toFixed(1)}</span></div>
        </div>`;
    });

    container.innerHTML = rows.join('');
}

// ── Sort ──────────────────────────────────────────────────────────────────────

function sortResults(key) {
    if (_sortKey === key) {
        _sortAsc = !_sortAsc;
    } else {
        _sortKey = key;
        _sortAsc = false;
    }
    renderResults();
}

// ── UI helpers ────────────────────────────────────────────────────────────────

function setStatus(type, html) {
    const el = document.getElementById('scan-status');
    el.className = `scan-status ${type}`;
    el.innerHTML = html;
}

function _rsiClass(rsi) {
    if (typeof rsi !== 'number') return 'neutral';
    if (rsi < 40) return 'negative';
    if (rsi > 65) return 'negative';
    return 'positive';
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
    return String(s ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function _timeAgo(isoStr) {
    try {
        const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
        if (diff < 60)    return `${Math.floor(diff)}s ago`;
        if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    } catch {
        return '';
    }
}
