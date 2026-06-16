// src/web/v2/analysis.js — v2 native Technical Analysis (Charts) tab
// Exposes window.AnalysisView.{render, teardown}
(function () {
    'use strict';

    const API_BASE = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || (() => {
        console.error('[Market Intelligence] FATAL: window.MARKET_INTELLIGENCE_CONFIG is not defined.');
        return '/MISSING_CONFIG_JS_SEE_CONSOLE';
    })();

    const INDICATOR_STORAGE_KEY = 'market-intelligence:csp-technical-analysis';
    const SCANNER_PARAMS_STORAGE_KEY = 'market-intelligence:csp-scanner-params';

    const DEFAULT_INDICATOR_SETTINGS = {
        sma: [
            { enabled: true, length: 20 },
            { enabled: true, length: 50 },
            { enabled: true, length: 200 },
        ],
        bollinger: { enabled: true, length: 20, multiplier: 2 },
        interval: 'D',
        theme: 'dark',
    };

    const ALLOWED_INTERVALS = new Set(['D', '240', 'W']);
    const ALLOWED_THEMES    = new Set(['dark', 'light']);

    // ── Module state ───────────────────────────────────────────────────────────
    let _watchlistCandidates = [];
    let _universeCandidates  = null;  // null = never fetched
    let _stockDataBySymbol   = {};
    let _activeSubTab        = 'watchlist';
    let _indicatorSettings   = DEFAULT_INDICATOR_SETTINGS;
    let _tvLibReady          = null;
    let _controlsChangeHandler = null;

    // ── Settings helpers ───────────────────────────────────────────────────────
    function _clampInt(v, fb, min, max)   { const n = parseInt(v, 10);   return (Number.isFinite(n) && n >= min && n <= max) ? n : fb; }
    function _clampFloat(v, fb, min, max) { const n = parseFloat(v);     return (Number.isFinite(n) && n >= min && n <= max) ? n : fb; }

    function _sanitize(raw) {
        raw = raw || {};
        const fb = DEFAULT_INDICATOR_SETTINGS;
        const rawSma = Array.isArray(raw.sma) ? raw.sma : fb.sma;
        return {
            sma: fb.sma.map((item, i) => ({
                enabled: typeof rawSma[i]?.enabled === 'boolean' ? rawSma[i].enabled : item.enabled,
                length:  _clampInt(rawSma[i]?.length, item.length, 2, 400),
            })),
            bollinger: {
                enabled:    typeof raw.bollinger?.enabled === 'boolean' ? raw.bollinger.enabled : fb.bollinger.enabled,
                length:     _clampInt(raw.bollinger?.length, fb.bollinger.length, 2, 400),
                multiplier: _clampFloat(raw.bollinger?.multiplier, fb.bollinger.multiplier, 0.1, 5),
            },
            interval: ALLOWED_INTERVALS.has(raw.interval) ? raw.interval : fb.interval,
            theme:    ALLOWED_THEMES.has(raw.theme)    ? raw.theme    : fb.theme,
        };
    }

    function _loadIndicatorSettings() {
        try {
            const raw = JSON.parse(localStorage.getItem(INDICATOR_STORAGE_KEY) || 'null');
            if (!raw) return { ...DEFAULT_INDICATOR_SETTINGS };
            if (raw.activeTab === 'universe') _activeSubTab = 'universe';
            return _sanitize(raw);
        } catch {
            return { ...DEFAULT_INDICATOR_SETTINGS };
        }
    }

    function _persistIndicatorSettings() {
        try {
            localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify({ ..._indicatorSettings, activeTab: _activeSubTab }));
        } catch { }
    }

    function _loadScannerParams() {
        const DEF = { min_cap: 10, max_price: 150, min_beta: 0.8, max_beta: 2.4, min_vol: 30, rsi_max: 50, adx_min: 15, adx_max: 50, dte_min: 3, dte_max: 46, conditions: [] };
        try {
            const raw = JSON.parse(localStorage.getItem(SCANNER_PARAMS_STORAGE_KEY) || 'null');
            if (!raw) return { ...DEF };
            const p = { ...DEF };
            ['min_cap','max_price','min_beta','max_beta','min_vol','rsi_max','adx_min','adx_max','dte_min','dte_max'].forEach(k => {
                if (typeof raw[k] === 'number') p[k] = raw[k];
            });
            if (Array.isArray(raw.conditions)) p.conditions = raw.conditions;
            return p;
        } catch { return { ...DEF }; }
    }

    function _buildScanQS(params) {
        const qs = new URLSearchParams({
            min_cap: params.min_cap, max_price: params.max_price, min_beta: params.min_beta,
            max_beta: params.max_beta, min_vol: params.min_vol, max_rsi: params.rsi_max,
            min_adx: params.adx_min, max_adx: params.adx_max, min_dte: params.dte_min, max_dte: params.dte_max,
        });
        if (params.conditions?.length) qs.set('conditions', params.conditions.join(','));
        return qs.toString();
    }

    function _buildWidgetStudies(settings) {
        const studies = [];
        const COLORS = ['rgba(255,179,0,1)', 'rgba(33,150,243,1)', 'rgba(76,175,80,1)'];
        let ci = 0;
        settings.sma.forEach(item => {
            if (item.enabled) {
                studies.push({ id: 'MASimple@tv-basicstudies', inputs: { length: item.length }, styles: { 'Plot.color': COLORS[ci] ?? 'rgba(255,255,255,1)', 'Plot.linewidth': 2 } });
                ci++;
            }
        });
        if (settings.bollinger.enabled) {
            studies.push({ id: 'BB@tv-basicstudies', inputs: { length: settings.bollinger.length, mult: settings.bollinger.multiplier } });
        }
        return studies;
    }

    // ── HTML template ──────────────────────────────────────────────────────────
    function _htmlTemplate() {
        const wlActive = _activeSubTab === 'watchlist';
        return `
<div class="anlys-view">
  <div class="section-header" style="padding-bottom:0">
    <span class="section-title">Charts</span>
  </div>
  <div class="watchlist-sub-tabs" style="padding:8px 16px 0">
    <button class="sub-tab${wlActive ? ' active' : ''} anlys-sub-tab" data-tab="watchlist">Watchlist</button>
    <button class="sub-tab${!wlActive ? ' active' : ''} anlys-sub-tab" data-tab="universe">Universe</button>
  </div>
  <div style="padding:8px 14px 10px;border-bottom:1px solid var(--tv-border)">
    <div class="scn-sheet-section-title" style="margin-bottom:6px">Indicators</div>
    <form id="anlys-controls" class="anlys-controls-grid"></form>
    <button id="anlys-refresh-btn" class="scn-sheet-rescan" style="width:100%;margin-top:8px;${wlActive ? 'display:none' : ''}" onclick="AnalysisView._refreshUniverse()">Refresh Universe</button>
  </div>
  <div id="anlys-message" style="padding:10px 14px;font-size:13px;color:var(--tv-muted);font-family:'IBM Plex Sans Condensed',sans-serif"></div>
  <div id="anlys-list" style="padding:0 0 80px"></div>
</div>`;
    }

    // ── Controls rendering ─────────────────────────────────────────────────────
    function _renderControls() {
        const form = document.getElementById('anlys-controls');
        if (!form) return;
        const s = _indicatorSettings;
        form.innerHTML = s.sma.map((item, i) => `
  <div class="anlys-ctrl-group">
    <label class="anlys-ctrl-label">
      <input type="checkbox" data-kind="sma-enabled" data-index="${i}" ${item.enabled ? 'checked' : ''}>
      <span>SMA${i + 1}</span>
    </label>
    <input type="number" class="scn-field-input anlys-ctrl-num" min="2" max="400" data-kind="sma-length" data-index="${i}" value="${item.length}">
  </div>`).join('') + `
  <div class="anlys-ctrl-group">
    <label class="anlys-ctrl-label">
      <input type="checkbox" data-kind="bb-enabled" ${s.bollinger.enabled ? 'checked' : ''}>
      <span>BB</span>
    </label>
    <input type="number" class="scn-field-input anlys-ctrl-num" min="2" max="400" data-kind="bb-length" value="${s.bollinger.length}">
    <input type="number" class="scn-field-input anlys-ctrl-num" min="0.1" max="5" step="0.1" data-kind="bb-multiplier" value="${s.bollinger.multiplier}">
  </div>`;
    }

    // ── Data loading ───────────────────────────────────────────────────────────
    function _setMessage(msg) {
        const el = document.getElementById('anlys-message');
        if (el) el.textContent = msg;
    }

    async function _loadStockData() {
        try {
            const r = await fetch(`${API_BASE}/screener/stocks`);
            if (!r.ok) return;
            const p = await r.json();
            const rows = Array.isArray(p.candidates) ? p.candidates : [];
            _stockDataBySymbol = Object.fromEntries(rows.map(row => [row.symbol, row]));
        } catch { }
    }

    async function _loadWatchlistCandidates() {
        _setMessage('Loading CSP candidates…');
        _destroyCharts();
        const list = document.getElementById('anlys-list');
        if (list) list.innerHTML = '';
        try {
            const r = await fetch(`${API_BASE}/screener/csp`);
            if (!r.ok) throw new Error(`Server error ${r.status}`);
            const p = await r.json();
            _watchlistCandidates = Array.isArray(p.candidates) ? p.candidates : [];
            if (_activeSubTab === 'watchlist') await _renderCandidates(_watchlistCandidates);
        } catch {
            if (_activeSubTab === 'watchlist') _setMessage('Unable to load CSP candidates.');
        }
    }

    async function _loadUniverseCandidates() {
        _setMessage('Loading universe candidates…');
        _destroyCharts();
        const list = document.getElementById('anlys-list');
        if (list) list.innerHTML = '';
        try {
            const params = _loadScannerParams();
            const r = await fetch(`${API_BASE}/screener/csp-scan?${_buildScanQS(params)}`);
            if (!r.ok) throw new Error(`Server error ${r.status}`);
            const p = await r.json();
            _universeCandidates = Array.isArray(p.candidates) ? p.candidates : [];
            if (_activeSubTab === 'universe') await _renderCandidates(_universeCandidates);
        } catch {
            if (_activeSubTab === 'universe') _setMessage('Unable to load universe candidates.');
        }
    }

    // ── Candidate rendering ────────────────────────────────────────────────────
    function _dedupe(arr) {
        const seen = new Set();
        return arr.filter(c => { if (!c?.symbol || seen.has(c.symbol)) return false; seen.add(c.symbol); return true; });
    }

    function _esc(v) {
        return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function _fmtMoney(v)  { return Number.isFinite(v) ? v.toFixed(2) : '—'; }
    function _fmtPct(v)    { return Number.isFinite(v) ? `${v.toFixed(1)}%` : '—'; }
    function _fmtNum(v)    { return (typeof v === 'number' && Number.isFinite(v)) ? v.toFixed(2) : (v && v !== 'N/A' ? String(v) : '—'); }
    function _fmtInt(v)    { return (Number.isFinite(v) && v > 0) ? v.toLocaleString() : '—'; }
    function _fmtFcf(v)    { if (typeof v !== 'number' || !Number.isFinite(v)) return '—'; return v >= 0 ? `$${v.toFixed(1)}B` : `-$${Math.abs(v).toFixed(1)}B`; }

    function _chip(label, value) {
        return `<div class="anlys-chip"><span class="anlys-chip-val">${_esc(value)}</span><span class="anlys-chip-lbl">${_esc(label)}</span></div>`;
    }

    async function _renderCandidates(source) {
        _destroyCharts();
        const list = document.getElementById('anlys-list');
        if (!list) return;
        const tickers = _dedupe(source);
        if (!tickers.length) {
            _setMessage('No candidates found.');
            list.innerHTML = '';
            return;
        }
        const label = _activeSubTab === 'universe' ? 'universe' : 'CSP';
        _setMessage(`${tickers.length} tickers from ${source.length} ${label} candidates`);

        list.innerHTML = tickers.map((c, i) => {
            const stock = _stockDataBySymbol[c.symbol];
            const clean = v => (v === 'N/A' || v == null) ? null : v;
            const atm_iv_rv = clean(stock?.atm_iv_rv20) ?? clean(c.atm_iv_rv20);
            const iv_pct    = clean(stock?.iv_percentile) ?? clean(c.iv_percentile);
            const pe        = clean(stock?.pe) ?? clean(c.pe);
            const exp    = c.expiration || '—';
            const strike = Number.isFinite(c.strike) ? `${c.strike.toFixed(2)} put` : '—';
            const dte    = Number.isFinite(c.dte) ? `${c.dte} DTE` : '—';
            return `
<div class="anlys-card" data-symbol="${_esc(c.symbol)}">
  <div class="anlys-card-header">
    <span class="anlys-symbol">${_esc(c.symbol || 'Unknown')}</span>
    <span class="anlys-subtitle">${_esc(exp)} · ${_esc(strike)} · ${_esc(dte)}</span>
  </div>
  <div class="anlys-chips">
    ${_chip('Score',   Number.isFinite(c.composite_score) ? c.composite_score.toFixed(1) : '—')}
    ${_chip('RSI',     _fmtNum(c.rsi))}
    ${_chip('ADX',     _fmtNum(c.adx))}
    ${_chip('IV/RV',   _fmtNum(atm_iv_rv))}
    ${_chip('IV%',     _fmtPct(iv_pct))}
    ${_chip('P/E',     _fmtNum(pe))}
    ${_chip('FCF',     _fmtFcf(c.fcf))}
  </div>
  <div class="anlys-chips" style="border-bottom:1px solid var(--tv-border);padding-bottom:8px">
    ${_chip('Price',     _fmtMoney(c.current_price))}
    ${_chip('Premium',   _fmtMoney(c.premium))}
    ${_chip('ROC',       _fmtPct(c.roc_percent))}
    ${_chip('Ann.Yld',   _fmtPct(c.annualized_roc))}
    ${_chip('%OTM',      _fmtPct(c.otm_percent))}
    ${_chip('IV',        _fmtPct(c.impliedVolatility))}
    ${_chip('Vol',       _fmtInt(c.volume))}
  </div>
  <div class="anlys-chart-container" id="anlys-chart-${i}"></div>
</div>`;
        }).join('');

        for (const [i, c] of tickers.entries()) {
            await _renderWidget(c, i);
        }
    }

    // ── TradingView widget ─────────────────────────────────────────────────────
    function _loadTvLib() {
        if (_tvLibReady) return _tvLibReady;
        _tvLibReady = new Promise((resolve, reject) => {
            if (window.TradingView) { resolve(); return; }
            const s = document.createElement('script');
            s.src = 'https://s3.tradingview.com/tv.js';
            s.onload = resolve;
            s.onerror = reject;
            document.head.appendChild(s);
        });
        return _tvLibReady;
    }

    async function _renderWidget(candidate, index) {
        const container = document.getElementById(`anlys-chart-${index}`);
        if (!container) return;
        try {
            await _loadTvLib();
        } catch {
            container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--tv-muted);font-size:13px;font-family:\'IBM Plex Sans Condensed\',sans-serif">Chart unavailable</div>';
            return;
        }
        new window.TradingView.widget({
            container_id:       `anlys-chart-${index}`,
            autosize:           true,
            symbol:             candidate.symbol || 'SPY',
            interval:           _indicatorSettings.interval,
            timezone:           'exchange',
            theme:              'dark',
            style:              '1',
            locale:             'en',
            backgroundColor:    'rgba(19,23,34,1)',
            withdateranges:     true,
            hide_side_toolbar:  true,
            allow_symbol_change: false,
            save_image:         false,
            studies:            _buildWidgetStudies(_indicatorSettings),
        });
    }

    function _destroyCharts() {
        document.querySelectorAll('.anlys-chart-container').forEach(el => { el.innerHTML = ''; });
    }

    // ── Sub-tab switching ──────────────────────────────────────────────────────
    async function _switchSubTab(tab) {
        if (tab === _activeSubTab) return;
        _activeSubTab = tab;
        _persistIndicatorSettings();
        document.querySelectorAll('.anlys-sub-tab').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tab);
        });
        const refreshBtn = document.getElementById('anlys-refresh-btn');
        if (refreshBtn) refreshBtn.style.display = tab === 'universe' ? '' : 'none';
        if (tab === 'watchlist') {
            await _renderCandidates(_watchlistCandidates);
        } else {
            if (_universeCandidates !== null) {
                await _renderCandidates(_universeCandidates);
            } else {
                await _loadUniverseCandidates();
            }
        }
    }

    async function _refreshUniverse() {
        _universeCandidates = null;
        await _loadUniverseCandidates();
    }

    // ── Init ───────────────────────────────────────────────────────────────────
    function _init() {
        document.querySelectorAll('.anlys-sub-tab').forEach(btn => {
            btn.addEventListener('click', () => _switchSubTab(btn.dataset.tab));
        });

        _renderControls();

        const form = document.getElementById('anlys-controls');
        if (form) {
            _controlsChangeHandler = async () => {
                const s = _indicatorSettings;
                _indicatorSettings = _sanitize({
                    sma: s.sma.map((item, i) => ({
                        enabled: form.querySelector(`[data-kind="sma-enabled"][data-index="${i}"]`)?.checked ?? item.enabled,
                        length:  form.querySelector(`[data-kind="sma-length"][data-index="${i}"]`)?.value  ?? item.length,
                    })),
                    bollinger: {
                        enabled:    form.querySelector('[data-kind="bb-enabled"]')?.checked    ?? s.bollinger.enabled,
                        length:     form.querySelector('[data-kind="bb-length"]')?.value       ?? s.bollinger.length,
                        multiplier: form.querySelector('[data-kind="bb-multiplier"]')?.value   ?? s.bollinger.multiplier,
                    },
                    interval: s.interval,
                    theme:    s.theme,
                });
                _persistIndicatorSettings();
                _renderControls();
                const source = _activeSubTab === 'universe' ? (_universeCandidates ?? []) : _watchlistCandidates;
                await _renderCandidates(source);
            };
            form.addEventListener('change', _controlsChangeHandler);
        }

        const fetches = _activeSubTab === 'universe'
            ? [_loadUniverseCandidates(), _loadStockData()]
            : [_loadWatchlistCandidates(), _loadStockData()];
        Promise.allSettled(fetches);
    }

    // ── Public API ─────────────────────────────────────────────────────────────
    function render(rootEl) {
        _watchlistCandidates = [];
        _universeCandidates  = null;
        _stockDataBySymbol   = {};
        _activeSubTab        = 'watchlist';
        _indicatorSettings   = _loadIndicatorSettings();
        rootEl.innerHTML     = _htmlTemplate();
        _init();
    }

    function teardown() {
        if (_controlsChangeHandler) {
            const form = document.getElementById('anlys-controls');
            if (form) form.removeEventListener('change', _controlsChangeHandler);
            _controlsChangeHandler = null;
        }
        _destroyCharts();
    }

    window.AnalysisView = { render, teardown, _switchSubTab, _refreshUniverse };
})();
