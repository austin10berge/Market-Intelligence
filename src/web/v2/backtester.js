// src/web/v2/backtester.js — v2 native Backtester tab
// Exposes window.BacktesterView.{render, teardown}
(function () {
    'use strict';

    const API_BASE = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || (() => {
        console.error('[Market Intelligence] FATAL: window.MARKET_INTELLIGENCE_CONFIG is not defined.');
        return '/MISSING_CONFIG_JS_SEE_CONSOLE';
    })();

    // ── Module state ───────────────────────────────────────────────────────────
    let conditionIdCounter = 0;
    let lastResult = null;
    let savedStrategiesById = {};
    let _inputHandler = null;
    let _changeHandler = null;

    const ASSET_PRESETS = {
        stock:        { enabled: false, type: 'call', position: 'long',  paired_stock: false },
        long_call:    { enabled: true,  type: 'call', position: 'long',  paired_stock: false },
        long_put:     { enabled: true,  type: 'put',  position: 'long',  paired_stock: false },
        short_put:    { enabled: true,  type: 'put',  position: 'short', paired_stock: false },
        short_call:   { enabled: true,  type: 'call', position: 'short', paired_stock: false },
        covered_call: { enabled: true,  type: 'call', position: 'short', paired_stock: true  },
    };

    // ── HTML template ──────────────────────────────────────────────────────────

    function _htmlTemplate() {
        return `
<div class="bkt-view">
  <div class="section-header" style="padding-bottom:0">
    <span class="section-title">Backtest</span>
    <div style="display:flex;align-items:center;gap:6px">
      <select id="bkt-saved-strategies" class="bkt-strategy-select" onchange="BacktesterView._loadStrategy()">
        <option value="">Load Strategy…</option>
      </select>
      <button id="bkt-delete-strategy-btn" class="bkt-icon-btn" onclick="BacktesterView._deleteStrategy()" style="display:none" title="Delete strategy">✕</button>
    </div>
  </div>

  <!-- Builder tabs -->
  <div class="watchlist-sub-tabs" style="padding:8px 16px 8px">
    <button class="sub-tab active bkt-tab-btn" data-tab="setup">Setup</button>
    <button class="sub-tab bkt-tab-btn" data-tab="strategy">Strategy</button>
    <button class="sub-tab bkt-tab-btn" data-tab="scalein">Scale-In</button>
  </div>

  <!-- Panel: Setup -->
  <div class="bkt-panel active" data-bkt-panel="setup">
    <div style="padding:0 14px 8px">
      <div class="scn-sheet-section-title" style="margin-bottom:8px">Asset Type</div>
      <div class="bkt-asset-grid" id="bkt-asset-radio-group">
        <button class="bkt-asset-btn" data-asset="stock" onclick="BacktesterView._selectAsset('stock')">Stock</button>
        <button class="bkt-asset-btn" data-asset="long_call" onclick="BacktesterView._selectAsset('long_call')">Long Call</button>
        <button class="bkt-asset-btn" data-asset="long_put" onclick="BacktesterView._selectAsset('long_put')">Long Put</button>
        <button class="bkt-asset-btn" data-asset="short_put" onclick="BacktesterView._selectAsset('short_put')">CSP</button>
        <button class="bkt-asset-btn" data-asset="short_call" onclick="BacktesterView._selectAsset('short_call')">Short Call</button>
        <button class="bkt-asset-btn" data-asset="covered_call" onclick="BacktesterView._selectAsset('covered_call')">Cov. Call</button>
      </div>
      <div id="bkt-options-fields" style="display:none;margin-top:8px">
        <div class="scn-field">
          <span class="scn-field-label">Target DTE</span>
          <input type="number" id="bkt-option-dte" class="scn-field-input" value="30" min="1" max="365">
        </div>
        <div class="scn-field">
          <span class="scn-field-label">Target Delta</span>
          <input type="number" id="bkt-option-delta" class="scn-field-input" value="0.50" step="0.05" min="0.01" max="1">
        </div>
        <div class="scn-field">
          <span class="scn-field-label">Roll Losing at DTE</span>
          <div style="display:flex;align-items:center;gap:8px">
            <label class="edit-toggle-wrapper" style="margin:0">
              <input type="checkbox" id="bkt-roll-enabled" class="edit-toggle-input" onchange="BacktesterView._toggleRollEnabled()">
              <span class="edit-toggle-track"></span>
            </label>
            <input type="number" id="bkt-roll-dte" class="scn-field-input" value="30" min="1" max="365" style="display:none;width:72px">
          </div>
        </div>
      </div>
    </div>

    <div style="padding:0 14px 8px;border-top:1px solid var(--tv-border)">
      <div class="scn-field">
        <span class="scn-field-label">Ticker</span>
        <input type="text" id="bkt-ticker" class="scn-field-input" placeholder="SPY" style="text-transform:uppercase">
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Start Date</span>
        <input type="date" id="bkt-start-date" class="scn-field-input">
      </div>
      <div class="scn-field">
        <span class="scn-field-label">End Date</span>
        <input type="date" id="bkt-end-date" class="scn-field-input">
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Timeframe</span>
        <select id="bkt-timeframe" class="scn-field-input">
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Capital ($)</span>
        <input type="number" id="bkt-capital" class="scn-field-input" value="10000" min="100">
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Commission ($)</span>
        <input type="number" id="bkt-commission" class="scn-field-input" value="0.0" step="0.01" min="0">
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Slippage (%)</span>
        <input type="number" id="bkt-slippage" class="scn-field-input" value="0.0" step="0.01" min="0">
      </div>
    </div>

    <div style="padding:0 14px 8px;border-top:1px solid var(--tv-border)">
      <div class="scn-sheet-section-title" style="margin-bottom:4px">Position Sizing</div>
      <div class="scn-field">
        <span class="scn-field-label">Method</span>
        <select id="bkt-sizing-method" class="scn-field-input" onchange="BacktesterView._toggleSizingInputs()">
          <option value="percent_equity">% of Equity</option>
          <option value="fixed_dollar">Fixed $</option>
          <option value="fixed_shares">Fixed Shares</option>
          <option value="risk_based">Risk Based</option>
        </select>
      </div>
      <div class="scn-field">
        <span class="scn-field-label" id="bkt-sizing-value-label">Percent (%)</span>
        <input type="number" id="bkt-sizing-value" class="scn-field-input" value="100">
      </div>
      <div id="bkt-risk-pct-container" class="scn-field" style="display:none">
        <span class="scn-field-label">Risk %</span>
        <input type="number" id="bkt-risk-pct" class="scn-field-input" value="1.0" step="0.1">
      </div>
    </div>

    <div style="padding:8px 14px 16px;border-top:1px solid var(--tv-border)">
      <div class="bkt-strategy-summary" id="bkt-strategy-summary"></div>
      <button class="scn-sheet-apply" style="width:100%;margin-top:8px" onclick="BacktesterView._run()">Run Backtest</button>
    </div>
  </div>

  <!-- Panel: Strategy -->
  <div class="bkt-panel" data-bkt-panel="strategy">
    <div style="padding:8px 14px 6px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--tv-border)">
      <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:var(--tv-muted)">Mode</span>
      <div class="bkt-toggle-group" id="bkt-strategy-mode-toggle">
        <button class="bkt-toggle-btn active" data-val="simple">Simple</button>
        <button class="bkt-toggle-btn" data-val="advanced">Advanced</button>
      </div>
    </div>

    <div style="padding:8px 14px 4px">
      <div class="scn-sheet-section-title" style="margin-bottom:4px">Entry Conditions</div>
      <div id="bkt-entry-warning" style="padding:10px 0;font-size:13px;color:var(--tv-yellow)">Add at least one entry condition.</div>
      <div style="display:none;align-items:center;gap:8px;margin-bottom:6px" class="bkt-advanced-only" id="bkt-entry-op-wrapper">
        <span style="font-size:13px;color:var(--tv-muted)">Operator:</span>
        <div class="bkt-toggle-group" id="bkt-entry-op-toggle">
          <button class="bkt-toggle-btn active" data-val="AND">AND</button>
          <button class="bkt-toggle-btn" data-val="OR">OR</button>
        </div>
      </div>
      <div id="bkt-entry-conditions"></div>
      <button class="bkt-add-cond-btn" onclick="BacktesterView._addCondition('bkt-entry-conditions')">+ Add Condition</button>
    </div>

    <div style="padding:8px 14px 4px;border-top:1px solid var(--tv-border)" class="bkt-advanced-only bkt-adv-block">
      <div class="scn-sheet-section-title" style="margin-bottom:4px">Exit Conditions</div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
        <span style="font-size:13px;color:var(--tv-muted)">Operator:</span>
        <div class="bkt-toggle-group" id="bkt-exit-op-toggle">
          <button class="bkt-toggle-btn active" data-val="AND">AND</button>
          <button class="bkt-toggle-btn" data-val="OR">OR</button>
        </div>
      </div>
      <div id="bkt-exit-conditions"></div>
      <button class="bkt-add-cond-btn" onclick="BacktesterView._addCondition('bkt-exit-conditions')">+ Add Condition</button>
    </div>

    <div style="padding:8px 14px 4px;border-top:1px solid var(--tv-border)">
      <div class="scn-sheet-section-title" style="margin-bottom:4px">Exit Rules</div>
      <div class="scn-field">
        <span class="scn-field-label">Stop Loss %</span>
        <input type="number" id="bkt-stop-loss" class="scn-field-input" placeholder="none" step="0.5" min="0">
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Take Profit %</span>
        <input type="number" id="bkt-take-profit" class="scn-field-input" placeholder="none" step="0.5" min="0">
      </div>
      <div class="scn-field bkt-advanced-only bkt-adv-flex">
        <span class="scn-field-label">Trailing Stop %</span>
        <input type="number" id="bkt-trailing-stop" class="scn-field-input" placeholder="none" step="0.5" min="0">
      </div>
      <div class="scn-field bkt-advanced-only bkt-adv-flex">
        <span class="scn-field-label">Max Hold (days)</span>
        <input type="number" id="bkt-max-hold" class="scn-field-input" placeholder="none" step="1" min="1">
      </div>
      <div class="scn-field bkt-advanced-only bkt-adv-flex">
        <span class="scn-field-label">Scale-In Exit</span>
        <select id="bkt-pyr-exit" class="scn-field-input">
          <option value="sell_all">All at Once</option>
          <option value="sell_profitable">Profitable Only</option>
        </select>
      </div>
    </div>

    <div style="padding:8px 14px 16px;border-top:1px solid var(--tv-border)">
      <button class="scn-sheet-apply" style="width:100%" onclick="BacktesterView._run()">Run Backtest</button>
    </div>
  </div>

  <!-- Panel: Scale-In -->
  <div class="bkt-panel" data-bkt-panel="scalein">
    <div style="padding:8px 14px">
      <div class="scn-field">
        <span class="scn-field-label">Enable Scale-In</span>
        <label class="edit-toggle-wrapper">
          <input type="checkbox" id="bkt-pyr-enabled" class="edit-toggle-input" onchange="BacktesterView._togglePyrInputs()">
          <span class="edit-toggle-track"></span>
        </label>
      </div>
      <div id="bkt-pyr-inputs" style="opacity:0.5;pointer-events:none">
        <div class="scn-field">
          <span class="scn-field-label">Max Tranches</span>
          <input type="number" id="bkt-pyr-max" class="scn-field-input" value="3" min="2" max="10">
        </div>
        <div class="scn-field">
          <span class="scn-field-label">Trigger Mode</span>
          <select id="bkt-pyr-trigger-mode" class="scn-field-input" onchange="BacktesterView._updatePyrFieldVisibility()">
            <option value="pullback_only">Pullback Only</option>
            <option value="entry_signal">Entry Signal</option>
            <option value="both">Both</option>
          </select>
        </div>
        <div id="bkt-pyr-pullback-pct-container" class="scn-field">
          <span class="scn-field-label">Pullback %</span>
          <input type="number" id="bkt-pyr-pullback-pct" class="scn-field-input" value="5.0" step="0.5" min="0.5">
        </div>
        <div id="bkt-pyr-pullback-ref-container" class="scn-field">
          <span class="scn-field-label">Reference High</span>
          <select id="bkt-pyr-pullback-ref" class="scn-field-input">
            <option value="entry">From Entry</option>
            <option value="rolling">Rolling High</option>
          </select>
        </div>
      </div>
    </div>
    <div style="padding:8px 14px 16px;border-top:1px solid var(--tv-border)">
      <button class="scn-sheet-apply" style="width:100%" onclick="BacktesterView._run()">Run Backtest</button>
    </div>
  </div>

  <!-- Loading overlay -->
  <div id="bkt-loading-overlay" class="bkt-loading-overlay" style="display:none">
    <div class="bkt-loading-card">
      <span class="spinner"></span>
      <span id="bkt-loading-msg">Running backtest…</span>
    </div>
  </div>

  <!-- Results (hidden until first run) -->
  <div id="bkt-results-panel" style="display:none">
    <div style="padding:12px 14px 6px;border-top:2px solid var(--tv-border)">
      <div style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;color:var(--tv-text);margin-bottom:2px" id="bkt-results-title"></div>
      <div style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tv-muted)" id="bkt-results-period"></div>
    </div>

    <div class="watchlist-sub-tabs" style="padding:0 16px 8px">
      <button class="sub-tab active bkt-res-tab" data-tab="summary">Summary</button>
      <button class="sub-tab bkt-res-tab" data-tab="position">Position</button>
      <button class="sub-tab bkt-res-tab" data-tab="trades">Trades</button>
      <button class="sub-tab bkt-res-tab" data-tab="wf">Walk-Fwd</button>
    </div>

    <!-- Summary -->
    <div class="bkt-res-panel" data-bkt-res="summary">
      <div class="bkt-stats-grid" id="bkt-stats-container" style="padding:0 14px 8px"></div>
      <div id="bkt-exit-reasons-card" style="display:none;margin:0 14px 8px;padding:10px 12px;background:var(--tv-surface);border:1px solid var(--tv-border);border-radius:8px">
        <div class="overview-card-title">Exit Reasons</div>
        <div id="bkt-exit-reasons-bars"></div>
      </div>
      <div class="bkt-chart-container" id="bkt-equity-chart"></div>
    </div>

    <!-- Position -->
    <div class="bkt-res-panel" data-bkt-res="position" style="display:none">
      <div class="bkt-chart-container" id="bkt-position-chart"></div>
    </div>

    <!-- Trades -->
    <div class="bkt-res-panel" data-bkt-res="trades" style="display:none">
      <div style="display:flex;align-items:center;gap:8px;padding:8px 14px 6px">
        <span style="font-size:13px;color:var(--tv-muted)">View:</span>
        <div class="bkt-toggle-group" id="bkt-trade-view-toggle">
          <button class="bkt-toggle-btn active" data-val="tranches">Tranches</button>
          <button class="bkt-toggle-btn" data-val="campaigns">Campaigns</button>
        </div>
      </div>
      <div style="overflow-x:auto">
        <table class="bkt-trades-table" id="bkt-trades-table">
          <thead id="bkt-trades-table-head"></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <!-- Walk-Forward -->
    <div class="bkt-res-panel" data-bkt-res="wf" style="display:none">
      <div style="padding:8px 14px" id="bkt-wf-controls">
        <div class="scn-field">
          <span class="scn-field-label">WF Mode</span>
          <select id="bkt-wf-mode" class="scn-field-input">
            <option value="rolling">Rolling</option>
            <option value="anchored">Anchored</option>
          </select>
        </div>
        <div class="scn-field">
          <span class="scn-field-label">In-Sample Days</span>
          <input type="number" id="bkt-wf-is-days" class="scn-field-input" value="504">
        </div>
        <div class="scn-field">
          <span class="scn-field-label">Out-of-Sample Days</span>
          <input type="number" id="bkt-wf-oos-days" class="scn-field-input" value="252">
        </div>
        <button class="scn-sheet-apply" style="width:100%;margin-top:8px" onclick="BacktesterView._runWalkForward()">Run Walk-Forward</button>
      </div>
      <div id="bkt-wf-results-content" style="display:none">
        <div style="padding:4px 14px 8px">
          <span class="bkt-badge" id="bkt-wf-mode-badge"></span>
        </div>
        <div class="bkt-stats-grid" id="bkt-wf-stats-container" style="padding:0 14px 8px"></div>
        <div class="bkt-chart-container" id="bkt-wf-equity-chart"></div>
        <div style="overflow-x:auto;padding:0 14px 8px">
          <table class="bkt-trades-table" id="bkt-wf-fold-table">
            <thead>
              <tr><th>Fold</th><th>IS Period</th><th>OOS Period</th><th>Regime</th><th>IS Ret%</th><th>OOS Ret%</th><th>Degrad.</th></tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Sweep + Save (below all result tabs) -->
    <div style="margin:0 14px 8px;padding:10px 12px;background:var(--tv-surface);border:1px solid var(--tv-border);border-radius:8px">
      <div class="overview-card-title">Parameter Sweep</div>
      <div class="scn-field">
        <span class="scn-field-label">Parameter</span>
        <select id="bkt-sweep-param" class="scn-field-input" style="width:auto;max-width:180px">
          <option value="exit.stop_loss_pct">Stop Loss %</option>
          <option value="exit.take_profit_pct">Take Profit %</option>
          <option value="exit.trailing_stop_pct">Trailing Stop %</option>
          <option value="exit.max_hold_days">Max Hold Days</option>
          <option value="pyramiding.pullback_pct">Pullback %</option>
          <option value="pyramiding.max_positions">Max Tranches</option>
          <option value="options.target_dte">Target DTE</option>
          <option value="options.target_delta">Target Delta</option>
          <option value="position_sizing.value">Sizing Value</option>
        </select>
      </div>
      <div class="scn-field">
        <span class="scn-field-label">Values</span>
        <input type="text" id="bkt-sweep-values" class="scn-field-input" placeholder="5 10 15 20">
      </div>
      <button class="scn-sheet-rescan" style="width:100%;margin-top:8px" onclick="BacktesterView._runSweep()">Run Sweep</button>
      <div id="bkt-sweep-results-content" style="display:none;margin-top:12px">
        <div style="font-size:12px;color:var(--tv-muted);margin-bottom:6px;font-family:'IBM Plex Mono',monospace">
          Sweep: <span id="bkt-sweep-param-label"></span>
        </div>
        <div class="bkt-chart-container" id="bkt-sweep-chart"></div>
        <div style="overflow-x:auto">
          <table class="bkt-trades-table" id="bkt-sweep-table">
            <thead><tr><th>Value</th><th>Return%</th><th>Sharpe</th><th>Max DD</th><th>Trades</th><th>Final $</th></tr></thead>
            <tbody></tbody>
          </table>
        </div>
      </div>
    </div>

    <div style="padding:0 14px 80px;display:flex;gap:8px">
      <button class="scn-sheet-rescan" style="flex:1" onclick="BacktesterView._saveStrategy()">Save Strategy</button>
    </div>
  </div>
</div>`;
    }

    // ── Asset type ─────────────────────────────────────────────────────────────

    function selectAsset(asset) {
        document.querySelectorAll('#bkt-asset-radio-group .bkt-asset-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.asset === asset);
        });
        const cfg = ASSET_PRESETS[asset];
        const optFields = document.getElementById('bkt-options-fields');
        if (optFields) optFields.style.display = cfg.enabled ? 'block' : 'none';
        updateStrategySummary();
    }

    function getSelectedAsset() {
        const btn = document.querySelector('#bkt-asset-radio-group .bkt-asset-btn.active');
        return btn ? btn.dataset.asset : 'stock';
    }

    // ── Strategy mode ──────────────────────────────────────────────────────────

    function applyStrategyMode(mode) {
        const isAdvanced = mode === 'advanced';
        document.querySelectorAll('.bkt-advanced-only').forEach(el => {
            if (!isAdvanced) {
                el.style.display = 'none';
            } else if (el.classList.contains('bkt-adv-flex')) {
                el.style.display = 'flex';
            } else if (el.classList.contains('bkt-adv-block')) {
                el.style.display = 'block';
            } else {
                el.style.display = '';
            }
        });
        // Update the wrapper that uses display:flex
        const entryOpWrap = document.getElementById('bkt-entry-op-wrapper');
        if (entryOpWrap) entryOpWrap.style.display = isAdvanced ? 'flex' : 'none';
        document.querySelectorAll('#bkt-strategy-mode-toggle .bkt-toggle-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.val === mode);
        });
    }

    // ── UI helpers ─────────────────────────────────────────────────────────────

    function toggleSizingInputs() {
        const method = document.getElementById('bkt-sizing-method')?.value;
        const label = document.getElementById('bkt-sizing-value-label');
        const input = document.getElementById('bkt-sizing-value');
        const riskCtr = document.getElementById('bkt-risk-pct-container');
        if (!method) return;
        if (method === 'percent_equity') {
            if (label) label.textContent = 'Percent (%)';
            if (input && !input.value) input.value = '100';
            if (riskCtr) riskCtr.style.display = 'none';
        } else if (method === 'fixed_dollar') {
            if (label) label.textContent = 'Dollar Amount ($)';
            if (input && !input.value) input.value = '10000';
            if (riskCtr) riskCtr.style.display = 'none';
        } else if (method === 'fixed_shares') {
            if (label) label.textContent = 'Number of Shares';
            if (input && !input.value) input.value = '100';
            if (riskCtr) riskCtr.style.display = 'none';
        } else if (method === 'risk_based') {
            if (label) label.textContent = 'Stop Distance (calc\'d)';
            if (input) { input.value = '0'; input.disabled = true; }
            if (riskCtr) riskCtr.style.display = 'flex';
        }
        if (method !== 'risk_based' && input) input.disabled = false;
    }

    function togglePyrInputs() {
        const enabled = document.getElementById('bkt-pyr-enabled')?.checked;
        const inputs = document.getElementById('bkt-pyr-inputs');
        if (inputs) {
            inputs.style.opacity = enabled ? '1' : '0.5';
            inputs.style.pointerEvents = enabled ? 'all' : 'none';
        }
        updateStrategySummary();
    }

    function toggleRollEnabled() {
        const enabled = document.getElementById('bkt-roll-enabled')?.checked;
        const rollDte = document.getElementById('bkt-roll-dte');
        if (rollDte) rollDte.style.display = enabled ? '' : 'none';
        updateStrategySummary();
    }

    function updatePyrFieldVisibility() {
        const mode = document.getElementById('bkt-pyr-trigger-mode')?.value;
        const show = mode === 'pullback_only' || mode === 'both';
        const pctCtr = document.getElementById('bkt-pyr-pullback-pct-container');
        const refCtr = document.getElementById('bkt-pyr-pullback-ref-container');
        if (pctCtr) pctCtr.style.display = show ? 'flex' : 'none';
        if (refCtr) refCtr.style.display = show ? 'flex' : 'none';
        updateStrategySummary();
    }

    function showLoading(show, msg = 'Running backtest…') {
        const overlay = document.getElementById('bkt-loading-overlay');
        const msgEl = document.getElementById('bkt-loading-msg');
        if (overlay) overlay.style.display = show ? 'flex' : 'none';
        if (msgEl) msgEl.textContent = msg;
    }

    // ── Formatters ─────────────────────────────────────────────────────────────

    function formatMoney(val) {
        if (val === null || val === undefined || val === 'N/A') return 'N/A';
        return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
    }

    function formatPct(val) {
        if (val === null || val === undefined || val === 'N/A') return 'N/A';
        return Number(val).toFixed(2) + '%';
    }

    // ── Strategy summary ───────────────────────────────────────────────────────

    function updateStrategySummary() {
        const summaryEl = document.getElementById('bkt-strategy-summary');
        if (!summaryEl) return;
        const ticker = (document.getElementById('bkt-ticker')?.value || '?').trim().toUpperCase();
        const asset = getSelectedAsset();
        const assetLabel = {
            stock: 'stock', long_call: 'long calls', long_put: 'long puts',
            short_put: 'short puts (CSP)', short_call: 'short calls',
            covered_call: 'covered calls',
        }[asset] || asset;
        const pyrOn = document.getElementById('bkt-pyr-enabled')?.checked;
        const pyrMode = document.getElementById('bkt-pyr-trigger-mode')?.value;
        const pullbackPct = parseFloat(document.getElementById('bkt-pyr-pullback-pct')?.value);
        let pyrPhrase = 'no scale-in';
        if (pyrOn) {
            const max = document.getElementById('bkt-pyr-max')?.value;
            if (pyrMode === 'pullback_only') pyrPhrase = `scale-in ×${max} on ${pullbackPct}% pullbacks`;
            else if (pyrMode === 'entry_signal') pyrPhrase = `scale-in ×${max} on entry signal`;
            else pyrPhrase = `scale-in ×${max} on pullbacks + signal`;
        }
        summaryEl.innerHTML = `<strong>${ticker}</strong> · ${assetLabel} · ${pyrPhrase}`;
    }

    // ── Condition builder ──────────────────────────────────────────────────────

    function addCondition(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const id = `bkt_cond_${conditionIdCounter++}`;
        const div = document.createElement('div');
        div.className = 'bkt-condition-node';
        div.id = id;
        div.innerHTML = `
      <select class="bkt-cond-select cond-indicator" onchange="BacktesterView._updateConditionParams('${id}')">
        <option value="RSI">RSI</option>
        <option value="SMA">SMA</option>
        <option value="EMA">EMA</option>
        <option value="MACD_LINE">MACD Line</option>
        <option value="MACD_HISTOGRAM">MACD Hist</option>
        <option value="BB_UPPER">BB Upper</option>
        <option value="BB_MIDDLE">BB Middle</option>
        <option value="BB_LOWER">BB Lower</option>
        <option value="PRICE">Price (Close)</option>
        <option value="VWAP">VWAP</option>
        <option value="PULLBACK">Pullback %</option>
        <option value="CONSECUTIVE">Consecutive</option>
      </select>
      <span class="cond-params" id="${id}_params">
        <input type="number" class="bkt-cond-input param-period" value="14" placeholder="Period">
      </span>
      <select class="bkt-cond-select cond-type" onchange="BacktesterView._updateConditionType('${id}')">
        <option value="threshold">vs Value</option>
        <option value="reference">vs Indicator</option>
        <option value="crossover">Crosses</option>
      </select>
      <span id="${id}_operator">
        <select class="bkt-cond-select cond-comparator">
          <option value="lt">&lt;</option>
          <option value="gt">&gt;</option>
          <option value="lte">&lt;=</option>
          <option value="gte">&gt;=</option>
        </select>
      </span>
      <span class="cond-target" id="${id}_target">
        <input type="number" class="bkt-cond-input target-value" value="30">
      </span>
      <button class="bkt-cond-remove" onclick="BacktesterView._removeCondition('${id}')" title="Remove">✕</button>`;
        container.appendChild(div);
        updateEntryWarning();
    }

    function removeCondition(rowId) {
        document.getElementById(rowId)?.remove();
        updateEntryWarning();
    }

    function updateEntryWarning() {
        const warning = document.getElementById('bkt-entry-warning');
        if (!warning) return;
        const has = document.getElementById('bkt-entry-conditions')?.querySelectorAll('.bkt-condition-node').length > 0;
        warning.style.display = has ? 'none' : 'block';
    }

    function updateConditionParams(rowId) {
        const row = document.getElementById(rowId);
        if (!row) return;
        const ind = row.querySelector('.cond-indicator').value;
        const paramsContainer = document.getElementById(`${rowId}_params`);
        const typeSelect = row.querySelector('.cond-type');

        typeSelect.style.display = 'block';
        document.getElementById(`${rowId}_operator`).style.display = 'inline';
        document.getElementById(`${rowId}_target`).style.display = 'inline';

        if (ind === 'PRICE' || ind === 'VWAP') {
            paramsContainer.innerHTML = '';
        } else if (ind === 'MACD_LINE' || ind === 'MACD_HISTOGRAM') {
            paramsContainer.innerHTML = `
          <input type="number" class="bkt-cond-input param-fast" value="12" title="Fast">
          <input type="number" class="bkt-cond-input param-slow" value="26" title="Slow">`;
        } else if (ind.startsWith('BB_')) {
            paramsContainer.innerHTML = `
          <input type="number" class="bkt-cond-input param-period" value="20">
          <input type="number" class="bkt-cond-input param-stddev" value="2.0" step="0.1">`;
        } else if (ind === 'PULLBACK') {
            paramsContainer.innerHTML = '';
            typeSelect.style.display = 'none';
            document.getElementById(`${rowId}_operator`).style.display = 'none';
            document.getElementById(`${rowId}_target`).innerHTML = `
          Drop <input type="number" class="bkt-cond-input pb-pct" value="5.0" style="width:52px"> %
          from <input type="number" class="bkt-cond-input pb-lookback" value="20" style="width:44px"> day high`;
        } else if (ind === 'CONSECUTIVE') {
            paramsContainer.innerHTML = '';
            typeSelect.style.display = 'none';
            document.getElementById(`${rowId}_operator`).style.display = 'none';
            document.getElementById(`${rowId}_target`).innerHTML = `
          <input type="number" class="bkt-cond-input cons-count" value="3" style="width:44px">
          <select class="bkt-cond-select cons-dir">
            <option value="down">Red</option>
            <option value="up">Green</option>
          </select> candles`;
        } else {
            paramsContainer.innerHTML = `<input type="number" class="bkt-cond-input param-period" value="14">`;
        }
    }

    function updateConditionType(rowId) {
        const row = document.getElementById(rowId);
        if (!row) return;
        const type = row.querySelector('.cond-type').value;
        const op = document.getElementById(`${rowId}_operator`);
        const tgt = document.getElementById(`${rowId}_target`);

        if (type === 'threshold') {
            op.innerHTML = `<select class="bkt-cond-select cond-comparator">
          <option value="lt">&lt;</option><option value="gt">&gt;</option>
          <option value="lte">&lt;=</option><option value="gte">&gt;=</option></select>`;
            tgt.innerHTML = `<input type="number" class="bkt-cond-input target-value" value="30">`;
        } else if (type === 'reference') {
            op.innerHTML = `<select class="bkt-cond-select cond-comparator">
          <option value="lt">&lt;</option><option value="gt">&gt;</option></select>`;
            tgt.innerHTML = `
          <select class="bkt-cond-select target-indicator">
            <option value="SMA">SMA</option><option value="EMA">EMA</option>
            <option value="VWAP">VWAP</option><option value="BB_UPPER">BB Upper</option>
            <option value="BB_MIDDLE">BB Middle</option><option value="BB_LOWER">BB Lower</option>
          </select>
          <input type="number" class="bkt-cond-input target-period" value="200">`;
        } else if (type === 'crossover') {
            op.innerHTML = `<select class="bkt-cond-select cond-direction">
          <option value="above">Above</option><option value="below">Below</option></select>`;
            tgt.innerHTML = `
          <select class="bkt-cond-select target-indicator">
            <option value="SMA">SMA</option><option value="EMA">EMA</option>
            <option value="VWAP">VWAP</option><option value="BB_UPPER">BB Upper</option>
            <option value="BB_MIDDLE">BB Middle</option><option value="BB_LOWER">BB Lower</option>
            <option value="MACD_SIGNAL">MACD Signal</option>
          </select>
          <input type="number" class="bkt-cond-input target-period" value="200">`;
        }
    }

    function serializeConditions(containerId, operatorId) {
        const container = document.getElementById(containerId);
        if (!container) return null;
        const rows = container.querySelectorAll('.bkt-condition-node');
        const conditions = [];

        rows.forEach(row => {
            const ind = row.querySelector('.cond-indicator').value;
            if (ind === 'PULLBACK') {
                conditions.push({ type: 'pullback', lookback: parseInt(row.querySelector('.pb-lookback').value), pct: parseFloat(row.querySelector('.pb-pct').value) });
                return;
            }
            if (ind === 'CONSECUTIVE') {
                conditions.push({ type: 'consecutive', count: parseInt(row.querySelector('.cons-count').value), direction: row.querySelector('.cons-dir').value });
                return;
            }
            const params = {};
            if (ind.startsWith('BB_')) {
                params.period = parseInt(row.querySelector('.param-period').value);
                params.std_dev = parseFloat(row.querySelector('.param-stddev').value);
            } else if (row.querySelector('.param-period')) {
                params.period = parseInt(row.querySelector('.param-period').value);
            } else if (row.querySelector('.param-fast')) {
                params.fast = parseInt(row.querySelector('.param-fast').value);
                params.slow = parseInt(row.querySelector('.param-slow').value);
            }
            const type = row.querySelector('.cond-type').value;
            const baseInd = { name: ind, params };
            if (type === 'threshold') {
                conditions.push({ type: 'threshold', indicator: baseInd, comparator: row.querySelector('.cond-comparator').value, value: parseFloat(row.querySelector('.target-value').value) });
            } else if (type === 'reference') {
                const rn = row.querySelector('.target-indicator').value;
                const rp = parseInt(row.querySelector('.target-period')?.value || 0);
                conditions.push({ type: 'reference', indicator: baseInd, comparator: row.querySelector('.cond-comparator').value, ref_indicator: { name: rn, params: rp ? { period: rp } : {} } });
            } else if (type === 'crossover') {
                const rn = row.querySelector('.target-indicator').value;
                const rp = parseInt(row.querySelector('.target-period')?.value || 0);
                conditions.push({ type: 'crossover', indicator: baseInd, direction: row.querySelector('.cond-direction').value, ref_indicator: { name: rn, params: rp ? { period: rp } : {} } });
            }
        });

        if (conditions.length === 0) return null;
        const operatorBtn = document.querySelector(`#${operatorId} .active`);
        const operator = operatorBtn ? operatorBtn.dataset.val : 'AND';
        return { operator, conditions };
    }

    function getActiveStrategyMode() {
        const btn = document.querySelector('#bkt-strategy-mode-toggle .active');
        return btn ? btn.dataset.val : 'simple';
    }

    // ── Payload builders ───────────────────────────────────────────────────────

    function buildStrategyPayload() {
        const entryTree = serializeConditions('bkt-entry-conditions', 'bkt-entry-op-toggle');
        const isAdvanced = getActiveStrategyMode() === 'advanced';
        const exitTree = isAdvanced ? serializeConditions('bkt-exit-conditions', 'bkt-exit-op-toggle') : null;
        const asset = getSelectedAsset();
        const optCfg = ASSET_PRESETS[asset];
        return {
            name: 'UI Strategy',
            entry: entryTree || { operator: 'AND', conditions: [] },
            exit: {
                conditions: exitTree,
                stop_loss_pct: parseFloat(document.getElementById('bkt-stop-loss')?.value) || null,
                take_profit_pct: parseFloat(document.getElementById('bkt-take-profit')?.value) || null,
                trailing_stop_pct: isAdvanced ? (parseFloat(document.getElementById('bkt-trailing-stop')?.value) || null) : null,
                max_hold_days: isAdvanced ? (parseInt(document.getElementById('bkt-max-hold')?.value) || null) : null,
                pyramiding_exit_mode: document.getElementById('bkt-pyr-exit')?.value || 'sell_all',
            },
            position_sizing: {
                method: document.getElementById('bkt-sizing-method')?.value || 'percent_equity',
                value: parseFloat(document.getElementById('bkt-sizing-value')?.value) || 100,
                risk_pct: parseFloat(document.getElementById('bkt-risk-pct')?.value) || null,
            },
            options: {
                enabled: optCfg.enabled,
                type: optCfg.type,
                position: optCfg.position,
                paired_stock: !!optCfg.paired_stock,
                target_dte: parseInt(document.getElementById('bkt-option-dte')?.value) || 30,
                target_delta: parseFloat(document.getElementById('bkt-option-delta')?.value) || 0.50,
                roll_dte: document.getElementById('bkt-roll-enabled')?.checked
                    ? (parseInt(document.getElementById('bkt-roll-dte')?.value) || 30)
                    : null,
            },
            pyramiding: {
                enabled: document.getElementById('bkt-pyr-enabled')?.checked || false,
                max_positions: parseInt(document.getElementById('bkt-pyr-max')?.value) || 3,
                trigger_mode: document.getElementById('bkt-pyr-trigger-mode')?.value || 'pullback_only',
                pullback_pct: parseFloat(document.getElementById('bkt-pyr-pullback-pct')?.value) || null,
                pullback_reference: document.getElementById('bkt-pyr-pullback-ref')?.value || 'entry',
            },
        };
    }

    function buildBacktestPayload() {
        return {
            strategy: buildStrategyPayload(),
            ticker: (document.getElementById('bkt-ticker')?.value || '').trim().toUpperCase(),
            start_date: document.getElementById('bkt-start-date')?.value,
            end_date: document.getElementById('bkt-end-date')?.value,
            timeframe: document.getElementById('bkt-timeframe')?.value || 'daily',
            initial_capital: parseFloat(document.getElementById('bkt-capital')?.value) || 10000,
            commission: parseFloat(document.getElementById('bkt-commission')?.value) || 0.0,
            slippage_pct: parseFloat(document.getElementById('bkt-slippage')?.value) || 0.0,
        };
    }

    // ── Execution ──────────────────────────────────────────────────────────────

    async function runBacktest() {
        if ((document.getElementById('bkt-entry-conditions')?.querySelectorAll('.bkt-condition-node').length || 0) === 0) {
            alert('Add at least one entry condition.');
            return;
        }
        const payload = buildBacktestPayload();
        showLoading(true, `Crunching ${payload.ticker}…`);
        try {
            const res = await fetch(`${API_BASE}/backtest/run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            const data = await res.json();
            lastResult = data;
            const panel = document.getElementById('bkt-results-panel');
            if (panel) panel.style.display = 'block';
            switchResultsTab('summary');
            renderStandardResults(data);
            const wfContent = document.getElementById('bkt-wf-results-content');
            if (wfContent) wfContent.style.display = 'none';
        } catch (e) {
            alert('Backtest failed: ' + e.message);
        } finally {
            showLoading(false);
        }
    }

    async function runWalkForward() {
        if ((document.getElementById('bkt-entry-conditions')?.querySelectorAll('.bkt-condition-node').length || 0) === 0) {
            alert('Add at least one entry condition.');
            return;
        }
        const payload = {
            ...buildBacktestPayload(),
            mode: document.getElementById('bkt-wf-mode')?.value || 'rolling',
            in_sample_days: parseInt(document.getElementById('bkt-wf-is-days')?.value) || 504,
            out_of_sample_days: parseInt(document.getElementById('bkt-wf-oos-days')?.value) || 252,
        };
        showLoading(true, `Running walk-forward for ${payload.ticker}…`);
        try {
            const res = await fetch(`${API_BASE}/backtest/walk-forward`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            renderWalkForwardResults(await res.json());
        } catch (e) {
            alert('Walk-forward failed: ' + e.message);
        } finally {
            showLoading(false);
        }
    }

    function switchResultsTab(name) {
        document.querySelectorAll('.bkt-res-tab').forEach(b => {
            b.classList.toggle('active', b.dataset.tab === name);
        });
        document.querySelectorAll('[data-bkt-res]').forEach(p => {
            p.style.display = p.dataset.bktRes === name ? 'block' : 'none';
        });
    }

    // ── Rendering ──────────────────────────────────────────────────────────────

    function renderStandardResults(data) {
        const titleEl = document.getElementById('bkt-results-title');
        const periodEl = document.getElementById('bkt-results-period');
        if (titleEl) titleEl.textContent = `Results: ${data.ticker}`;
        if (periodEl) periodEl.textContent = `${data.start_date} → ${data.end_date}`;
        renderStats(data.stats, 'bkt-stats-container');
        renderExitReasons(data.stats?.exit_reasons || {}, data.trades || []);
        renderEquityCurve(data.equity_curve, data.benchmark_curve, 'bkt-equity-chart');
        renderPositionChart(data, 'bkt-position-chart');
        renderTradesTable(data.trades || []);
    }

    function renderExitReasons(exitReasonsCounts, trades) {
        const card = document.getElementById('bkt-exit-reasons-card');
        const container = document.getElementById('bkt-exit-reasons-bars');
        if (!card || !container) return;
        const total = trades.length;
        if (total === 0) { card.style.display = 'none'; return; }
        card.style.display = 'block';
        const pnlByReason = {};
        trades.forEach(t => {
            if (!pnlByReason[t.exit_reason]) pnlByReason[t.exit_reason] = [];
            pnlByReason[t.exit_reason].push(t.pnl);
        });
        const palette = {
            take_profit: '#22c55e', stop_loss: '#ef4444', trailing_stop: '#f97316',
            expiration: '#a78bfa', time: '#94a3b8', signal: '#3b82f6',
            end_of_data: '#64748b', sibling_profit_take: '#10b981',
        };
        container.innerHTML = Object.entries(exitReasonsCounts)
            .sort((a, b) => b[1] - a[1])
            .map(([reason, count]) => {
                const pnls = pnlByReason[reason] || [];
                const avgPnl = pnls.length ? pnls.reduce((s, p) => s + p, 0) / pnls.length : 0;
                const pct = (count / total) * 100;
                const color = palette[reason] || '#60a5fa';
                return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
              <div style="flex:0 0 110px;font-size:12px;text-transform:capitalize">${reason.replace(/_/g, ' ')}</div>
              <div style="flex:1;background:rgba(255,255,255,0.05);height:18px;border-radius:3px;overflow:hidden">
                <div style="width:${pct.toFixed(1)}%;height:100%;background:${color}"></div>
              </div>
              <div style="flex:0 0 50px;text-align:right;font-family:'IBM Plex Mono',monospace;font-size:11px">${count} (${pct.toFixed(0)}%)</div>
              <div style="flex:0 0 80px;text-align:right;font-family:'IBM Plex Mono',monospace;font-size:11px;color:${avgPnl >= 0 ? 'var(--tv-green)' : 'var(--tv-red)'}">${formatMoney(avgPnl)}</div>
            </div>`;
            }).join('');
    }

    function renderWalkForwardResults(data) {
        const content = document.getElementById('bkt-wf-results-content');
        if (content) content.style.display = 'block';
        const modeBadge = document.getElementById('bkt-wf-mode-badge');
        if (modeBadge) modeBadge.textContent = data.mode.toUpperCase();
        renderStats(data.aggregated_oos_stats || {}, 'bkt-wf-stats-container');
        renderEquityCurve(data.aggregated_oos_curve || [], null, 'bkt-wf-equity-chart');
        const tbody = document.querySelector('#bkt-wf-fold-table tbody');
        if (!tbody) return;
        tbody.innerHTML = '';
        (data.folds || []).forEach(f => {
            const deg = data.degradation_ratios?.sharpe_ratio;
            const degStr = deg != null ? Number(deg).toFixed(2) : 'N/A';
            const tr = document.createElement('tr');
            const regCls = f.regime === 'bull' ? 'bkt-badge-green' : f.regime === 'bear' ? 'bkt-badge-red' : 'bkt-badge-blue';
            tr.innerHTML = `
          <td>Fold ${f.fold_number}</td>
          <td style="white-space:nowrap">${f.is_start} → ${f.is_end}</td>
          <td style="white-space:nowrap">${f.oos_start} → ${f.oos_end}</td>
          <td><span class="bkt-badge ${regCls}">${f.regime.toUpperCase()}</span></td>
          <td class="${f.is_stats.total_return_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(f.is_stats.total_return_pct)}</td>
          <td class="${f.oos_stats.total_return_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(f.oos_stats.total_return_pct)}</td>
          <td>${degStr}</td>`;
            tbody.appendChild(tr);
        });
        switchResultsTab('wf');
    }

    function renderStats(stats, containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const items = [
            { label: 'Total Return', val: formatPct(stats.total_return_pct), cls: stats.total_return_pct >= 0 ? 'text-green' : 'text-red' },
            { label: 'CAGR', val: stats.cagr === 'N/A' ? 'N/A' : formatPct(stats.cagr), cls: (stats.cagr === 'N/A' || stats.cagr >= 0) ? 'text-green' : 'text-red' },
            { label: 'Sharpe', val: stats.sharpe_ratio, cls: stats.sharpe_ratio >= 1.0 ? 'text-green' : '' },
            { label: 'Sortino', val: stats.sortino_ratio, cls: '' },
            { label: 'Max Drawdown', val: stats.max_drawdown_pct != null ? '-' + formatPct(stats.max_drawdown_pct) : 'N/A', cls: 'text-red' },
            { label: 'Win Rate', val: formatPct(stats.win_rate_pct), cls: stats.win_rate_pct >= 50 ? 'text-green' : 'text-red' },
            { label: 'Trades', val: stats.total_trades, cls: '' },
            { label: 'Profit Factor', val: stats.profit_factor, cls: '' },
            { label: 'Alpha vs B&H', val: stats.alpha === 'N/A' ? 'N/A' : formatPct(stats.alpha), cls: (stats.alpha === 'N/A' || stats.alpha >= 0) ? 'text-green' : 'text-red' },
        ];
        container.innerHTML = items.map(item => `
      <div class="bkt-stat-card">
        <div class="bkt-stat-label">${item.label}</div>
        <div class="bkt-stat-val ${item.cls}">${item.val}</div>
      </div>`).join('');
    }

    function getActiveTradeView() {
        const btn = document.querySelector('#bkt-trade-view-toggle .active');
        return btn ? btn.dataset.val : 'tranches';
    }

    function groupTradesByCampaign(trades) {
        const groups = new Map();
        trades.forEach(t => {
            const key = t.campaign_id || 0;
            if (!groups.has(key)) {
                groups.set(key, {
                    campaign_id: key, entry_date: t.entry_date, exit_date: t.exit_date,
                    tranches: 0, contracts_or_shares: 0, cost_basis: 0, proceeds: 0,
                    pnl: 0, is_option: t.is_option, exit_reasons: new Set(), direction: t.direction,
                });
            }
            const g = groups.get(key);
            const m = t.is_option ? 100 : 1;
            g.tranches++;
            g.contracts_or_shares += t.shares;
            g.cost_basis += t.shares * t.entry_price * m;
            g.proceeds += t.shares * t.exit_price * m;
            g.pnl += t.pnl;
            g.entry_date = g.entry_date < t.entry_date ? g.entry_date : t.entry_date;
            g.exit_date = g.exit_date > t.exit_date ? g.exit_date : t.exit_date;
            g.exit_reasons.add(t.exit_reason);
        });
        const rows = [...groups.values()];
        rows.forEach(g => {
            const m = g.is_option ? 100 : 1;
            g.avg_entry = g.cost_basis / g.contracts_or_shares / m;
            g.avg_exit = g.proceeds / g.contracts_or_shares / m;
            g.pnl_pct = g.cost_basis > 0 ? (g.pnl / g.cost_basis) * 100 : 0;
        });
        return rows.sort((a, b) => b.entry_date.localeCompare(a.entry_date));
    }

    function renderTradesTable(trades) {
        const tbody = document.querySelector('#bkt-trades-table tbody');
        const thead = document.getElementById('bkt-trades-table-head');
        if (!tbody || !thead) return;
        tbody.innerHTML = '';
        const hasOptions = trades.some(t => t.is_option);
        const view = getActiveTradeView();

        if (view === 'campaigns') {
            thead.innerHTML = `<tr><th>Entry</th><th>Exit</th><th>Tranches</th><th>${hasOptions ? 'Contracts' : 'Shares'}</th><th>Avg Entry</th><th>Avg Exit</th><th>P&amp;L%</th><th>P&amp;L$</th><th>Exits</th></tr>`;
            groupTradesByCampaign(trades).forEach(g => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
            <td>${g.entry_date}</td><td>${g.exit_date}</td><td>${g.tranches}</td>
            <td>${g.contracts_or_shares}</td><td>${formatMoney(g.avg_entry)}</td><td>${formatMoney(g.avg_exit)}</td>
            <td class="${g.pnl_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(g.pnl_pct)}</td>
            <td class="${g.pnl >= 0 ? 'text-green' : 'text-red'}">${formatMoney(g.pnl)}</td>
            <td>${[...g.exit_reasons].map(r => `<span class="bkt-badge bkt-badge-blue">${r.replace(/_/g, ' ')}</span>`).join(' ')}</td>`;
                tbody.appendChild(tr);
            });
            return;
        }

        if (hasOptions) {
            thead.innerHTML = `<tr><th>Entry</th><th>Exit</th><th>Side</th><th>Strike</th><th>Contracts</th><th>Entry Prem</th><th>Exit Prem</th><th>Cost</th><th>P&amp;L%</th><th>P&amp;L$</th><th>Reason</th></tr>`;
        } else {
            thead.innerHTML = `<tr><th>Entry</th><th>Exit</th><th>Dir</th><th>Entry $</th><th>Exit $</th><th>Shares</th><th>P&amp;L%</th><th>P&amp;L$</th><th>Reason</th></tr>`;
        }
        [...trades].reverse().forEach(t => {
            const tr = document.createElement('tr');
            if (t.is_option) {
                const side = (t.option_position || 'long').toUpperCase() + ' ' + (t.option_type || '').toUpperCase();
                tr.innerHTML = `
            <td>${t.entry_date}</td><td>${t.exit_date}</td><td>${side}</td>
            <td>${t.option_strike ?? ''}</td><td>${t.shares}</td>
            <td>${formatMoney(t.entry_price)}</td><td>${formatMoney(t.exit_price)}</td>
            <td>${formatMoney(t.shares * t.entry_price * 100)}</td>
            <td class="${t.pnl_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(t.pnl_pct)}</td>
            <td class="${t.pnl >= 0 ? 'text-green' : 'text-red'}">${formatMoney(t.pnl)}</td>
            <td><span class="bkt-badge bkt-badge-blue">${t.exit_reason.replace(/_/g, ' ')}</span></td>`;
            } else {
                tr.innerHTML = `
            <td>${t.entry_date}</td><td>${t.exit_date}</td><td>${t.direction.toUpperCase()}</td>
            <td>${formatMoney(t.entry_price)}</td><td>${formatMoney(t.exit_price)}</td><td>${t.shares}</td>
            <td class="${t.pnl_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(t.pnl_pct)}</td>
            <td class="${t.pnl >= 0 ? 'text-green' : 'text-red'}">${formatMoney(t.pnl)}</td>
            <td><span class="bkt-badge bkt-badge-blue">${t.exit_reason.replace(/_/g, ' ')}</span></td>`;
            }
            tbody.appendChild(tr);
        });
    }

    // ── Charts ─────────────────────────────────────────────────────────────────

    function makeChart(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return null;
        container.innerHTML = '';
        if (!window.LightweightCharts) {
            container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--tv-muted);font-size:13px">Chart library not loaded</div>';
            return null;
        }
        const chart = window.LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 280,
            layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8' },
            grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
            timeScale: { borderColor: 'rgba(255,255,255,0.1)' },
            rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
        });
        const resizer = () => chart.applyOptions({ width: container.clientWidth });
        window.addEventListener('resize', resizer);
        chart.__resizer = resizer;
        return { chart, container };
    }

    function renderEquityCurve(curveData, benchData, containerId) {
        const result = makeChart(containerId);
        if (!result) return;
        const { chart } = result;
        const eqSeries = chart.addAreaSeries({
            lineColor: '#3b82f6', topColor: 'rgba(59,130,246,0.4)', bottomColor: 'rgba(59,130,246,0.0)', lineWidth: 2,
        });
        eqSeries.setData((curveData || []).map(d => ({ time: d.date, value: d.equity })));
        if (benchData) {
            const bSeries = chart.addLineSeries({ color: '#94a3b8', lineWidth: 1, lineStyle: 2 });
            bSeries.setData(benchData.map(d => ({ time: d.date, value: d.equity })));
        }
        chart.timeScale().fitContent();
    }

    function renderPositionChart(data, containerId) {
        const bench = data.benchmark_curve || [];
        if (!bench.length) {
            const el = document.getElementById(containerId);
            if (el) el.innerHTML = '<div style="padding:24px;text-align:center;color:var(--tv-muted);font-size:13px">No price history to plot.</div>';
            return;
        }
        const firstEquity = bench[0].equity;
        if (firstEquity <= 0) return;
        const underlyingNorm = bench.map(d => ({ time: d.date, value: d.equity / firstEquity }));

        const trades = data.trades || [];
        let scale = 1.0;
        if (trades.length > 0 && trades[0].entry_underlying_price > 0) {
            const t0 = trades[0];
            const normEntry = underlyingNorm.find(p => p.time === t0.entry_date);
            if (normEntry && normEntry.value > 0) scale = t0.entry_underlying_price / normEntry.value;
        } else if (data.equity_curve?.length) {
            scale = data.initial_capital;
        }
        const priceData = underlyingNorm.map(d => ({ time: d.time, value: d.value * scale }));

        const result = makeChart(containerId);
        if (!result) return;
        const { chart } = result;
        const priceSeries = chart.addLineSeries({ color: '#60a5fa', lineWidth: 2, title: 'Close' });
        priceSeries.setData(priceData);

        const markers = [];
        trades.forEach(t => {
            const isShortOpt = t.is_option && t.option_position === 'short';
            markers.push({ time: t.entry_date, position: 'belowBar', color: isShortOpt ? '#f97316' : '#22c55e', shape: 'arrowUp', text: `Open${t.is_option ? ' ' + (t.option_position || 'long')[0].toUpperCase() : ''}` });
            markers.push({ time: t.exit_date, position: 'aboveBar', color: t.pnl >= 0 ? '#22c55e' : '#ef4444', shape: 'arrowDown', text: t.exit_reason.replace(/_/g, ' ') });
        });
        markers.sort((a, b) => a.time.localeCompare(b.time));
        priceSeries.setMarkers(markers);

        const eq = data.equity_curve || [];
        const avgCostPoints = eq.filter(d => d.avg_cost != null).map(d => ({ time: d.date, value: d.avg_cost }));
        if (avgCostPoints.length > 0) {
            const avgSeries = chart.addLineSeries({ color: '#f59e0b', lineWidth: 1, title: 'Avg cost' });
            avgSeries.setData(avgCostPoints);
        }

        trades.filter(t => t.is_option && t.option_strike != null).forEach(t => {
            const strikeSeries = chart.addLineSeries({ color: 'rgba(148,163,184,0.55)', lineWidth: 1, lineStyle: 2, crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false });
            strikeSeries.setData([{ time: t.entry_date, value: t.option_strike }, { time: t.exit_date, value: t.option_strike }]);
            const sign = t.option_type === 'call' ? 1 : -1;
            const be = t.option_strike + sign * t.entry_price;
            const beSeries = chart.addLineSeries({ color: 'rgba(234,179,8,0.55)', lineWidth: 1, lineStyle: 3, crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false });
            beSeries.setData([{ time: t.entry_date, value: be }, { time: t.exit_date, value: be }]);
        });
        chart.timeScale().fitContent();
    }

    // ── Saved strategies ───────────────────────────────────────────────────────

    async function loadSavedStrategiesList() {
        try {
            const res = await fetch(`${API_BASE}/backtest/strategies`);
            if (!res.ok) return;
            const data = await res.json();
            const select = document.getElementById('bkt-saved-strategies');
            if (!select) return;
            select.innerHTML = '<option value="">Load Strategy…</option>';
            savedStrategiesById = {};
            (data.strategies || []).forEach(s => {
                savedStrategiesById[s.id] = s;
                const opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = s.name;
                select.appendChild(opt);
            });
        } catch (e) {
            console.warn('Could not load strategies:', e);
        }
    }

    function loadSelectedStrategy() {
        const sel = document.getElementById('bkt-saved-strategies');
        const deleteBtn = document.getElementById('bkt-delete-strategy-btn');
        const id = sel?.value;
        if (!id) { if (deleteBtn) deleteBtn.style.display = 'none'; return; }
        if (deleteBtn) deleteBtn.style.display = 'inline-block';
        const record = savedStrategiesById[id];
        if (!record?.definition) { alert('Strategy definition missing.'); return; }
        applyStrategyToForm(record.definition);
        updateStrategySummary();
    }

    async function deleteSelectedStrategy() {
        const sel = document.getElementById('bkt-saved-strategies');
        const id = sel?.value;
        if (!id) return;
        const name = savedStrategiesById[id]?.name || 'this strategy';
        if (!confirm(`Delete "${name}"?`)) return;
        try {
            const res = await fetch(`${API_BASE}/backtest/strategies/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            await loadSavedStrategiesList();
            const deleteBtn = document.getElementById('bkt-delete-strategy-btn');
            if (deleteBtn) deleteBtn.style.display = 'none';
        } catch (e) {
            alert('Failed to delete: ' + e.message);
        }
    }

    function inferAssetFromDef(opt) {
        if (!opt || !opt.enabled) return 'stock';
        if (opt.paired_stock && opt.type === 'call' && opt.position === 'short') return 'covered_call';
        const isShort = opt.position === 'short';
        if (opt.type === 'call') return isShort ? 'short_call' : 'long_call';
        return isShort ? 'short_put' : 'long_put';
    }

    function applyStrategyToForm(def) {
        const sizing = def.position_sizing || {};
        if (sizing.method) { const el = document.getElementById('bkt-sizing-method'); if (el) el.value = sizing.method; }
        toggleSizingInputs();
        if (sizing.value != null) { const el = document.getElementById('bkt-sizing-value'); if (el) el.value = sizing.value; }
        if (sizing.risk_pct != null) { const el = document.getElementById('bkt-risk-pct'); if (el) el.value = sizing.risk_pct; }

        const ex = def.exit || {};
        ['bkt-stop-loss', 'bkt-take-profit', 'bkt-trailing-stop', 'bkt-max-hold'].forEach((id, i) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.value = [ex.stop_loss_pct, ex.take_profit_pct, ex.trailing_stop_pct, ex.max_hold_days][i] ?? '';
        });
        if (ex.pyramiding_exit_mode) { const el = document.getElementById('bkt-pyr-exit'); if (el) el.value = ex.pyramiding_exit_mode; }

        selectAsset(inferAssetFromDef(def.options));
        const opt = def.options || {};
        if (opt.target_dte != null) { const el = document.getElementById('bkt-option-dte'); if (el) el.value = opt.target_dte; }
        if (opt.target_delta != null) { const el = document.getElementById('bkt-option-delta'); if (el) el.value = opt.target_delta; }
        const rollEnabled = document.getElementById('bkt-roll-enabled');
        const rollDte = document.getElementById('bkt-roll-dte');
        if (rollEnabled && rollDte) {
            rollEnabled.checked = opt.roll_dte != null;
            rollDte.style.display = opt.roll_dte != null ? '' : 'none';
            if (opt.roll_dte != null) rollDte.value = opt.roll_dte;
        }

        const pyr = def.pyramiding || {};
        const pyrEnabled = document.getElementById('bkt-pyr-enabled');
        if (pyrEnabled) pyrEnabled.checked = !!pyr.enabled;
        if (pyr.max_positions != null) { const el = document.getElementById('bkt-pyr-max'); if (el) el.value = pyr.max_positions; }
        let triggerMode = pyr.trigger_mode;
        if (!triggerMode && pyr.scale_in_trigger) triggerMode = pyr.scale_in_trigger === 'pullback' ? 'pullback_only' : 'entry_signal';
        if (triggerMode) { const el = document.getElementById('bkt-pyr-trigger-mode'); if (el) el.value = triggerMode; }
        const pullbackPct = pyr.pullback_pct ?? pyr.scale_in_value;
        if (pullbackPct != null) { const el = document.getElementById('bkt-pyr-pullback-pct'); if (el) el.value = pullbackPct; }
        if (pyr.pullback_reference) { const el = document.getElementById('bkt-pyr-pullback-ref'); if (el) el.value = pyr.pullback_reference; }
        togglePyrInputs();
        updatePyrFieldVisibility();

        const entryConds = def.entry?.conditions || [];
        const exitConds = ex.conditions?.conditions || [];
        const needsAdvanced = entryConds.length > 1 || exitConds.length > 0 || ex.trailing_stop_pct != null || ex.max_hold_days != null;
        applyStrategyMode(needsAdvanced ? 'advanced' : 'simple');
        rebuildConditionsFromTree('bkt-entry-conditions', 'bkt-entry-op-toggle', def.entry);
        rebuildConditionsFromTree('bkt-exit-conditions', 'bkt-exit-op-toggle', ex.conditions);
        updateEntryWarning();
    }

    function rebuildConditionsFromTree(containerId, toggleId, tree) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';
        if (!tree?.conditions?.length) return;
        const op = (tree.operator || 'AND').toUpperCase();
        document.querySelectorAll(`#${toggleId} .bkt-toggle-btn`).forEach(b => {
            b.classList.toggle('active', b.dataset.val === op);
        });
        tree.conditions.forEach(c => {
            addCondition(containerId);
            const lastRow = container.querySelector('.bkt-condition-node:last-child');
            applyConditionToRow(lastRow, c);
        });
    }

    function applyConditionToRow(row, cond) {
        if (!row) return;
        const indSel = row.querySelector('.cond-indicator');
        if (cond.type === 'pullback') {
            indSel.value = 'PULLBACK';
            updateConditionParams(row.id);
            const pct = row.querySelector('.pb-pct');
            const lb = row.querySelector('.pb-lookback');
            if (pct) pct.value = cond.pct;
            if (lb) lb.value = cond.lookback;
            return;
        }
        if (cond.type === 'consecutive') {
            indSel.value = 'CONSECUTIVE';
            updateConditionParams(row.id);
            const cnt = row.querySelector('.cons-count');
            const dir = row.querySelector('.cons-dir');
            if (cnt) cnt.value = cond.count;
            if (dir) dir.value = cond.direction;
            return;
        }
        const indName = (cond.indicator?.name || 'RSI').toUpperCase();
        indSel.value = indName;
        updateConditionParams(row.id);
        const params = cond.indicator?.params || {};
        const pp = row.querySelector('.param-period');
        const ps = row.querySelector('.param-stddev');
        const pf = row.querySelector('.param-fast');
        const psl = row.querySelector('.param-slow');
        if (pp && params.period != null) pp.value = params.period;
        if (ps && params.std_dev != null) ps.value = params.std_dev;
        if (pf && params.fast != null) pf.value = params.fast;
        if (psl && params.slow != null) psl.value = params.slow;
        const typeSel = row.querySelector('.cond-type');
        typeSel.value = cond.type || 'threshold';
        updateConditionType(row.id);
        if (cond.type === 'threshold') {
            const cmp = row.querySelector('.cond-comparator');
            const tv = row.querySelector('.target-value');
            if (cmp) cmp.value = cond.comparator || 'lt';
            if (tv) tv.value = cond.value ?? 0;
        } else if (cond.type === 'reference') {
            const cmp = row.querySelector('.cond-comparator');
            const rn = row.querySelector('.target-indicator');
            const rp = row.querySelector('.target-period');
            if (cmp) cmp.value = cond.comparator || 'gt';
            if (rn && cond.ref_indicator?.name) rn.value = cond.ref_indicator.name;
            if (rp && cond.ref_indicator?.params?.period != null) rp.value = cond.ref_indicator.params.period;
        } else if (cond.type === 'crossover') {
            const ds = row.querySelector('.cond-direction');
            const rn = row.querySelector('.target-indicator');
            const rp = row.querySelector('.target-period');
            if (ds) ds.value = cond.direction || 'above';
            if (rn && cond.ref_indicator?.name) rn.value = cond.ref_indicator.name;
            if (rp && cond.ref_indicator?.params?.period != null) rp.value = cond.ref_indicator.params.period;
        }
    }

    // ── Parameter sweep ────────────────────────────────────────────────────────

    const SWEEP_PARAM_LABELS = {
        'exit.stop_loss_pct': 'Stop Loss %', 'exit.take_profit_pct': 'Take Profit %',
        'exit.trailing_stop_pct': 'Trailing Stop %', 'exit.max_hold_days': 'Max Hold Days',
        'pyramiding.pullback_pct': 'Pullback %', 'pyramiding.max_positions': 'Max Tranches',
        'options.target_dte': 'Target DTE', 'options.target_delta': 'Target Delta',
        'position_sizing.value': 'Sizing Value',
    };

    async function runParameterSweep() {
        if ((document.getElementById('bkt-entry-conditions')?.querySelectorAll('.bkt-condition-node').length || 0) === 0) {
            alert('Add at least one entry condition.');
            return;
        }
        const param = document.getElementById('bkt-sweep-param')?.value;
        const raw = document.getElementById('bkt-sweep-values')?.value?.trim() || '';
        const values = raw.split(/[,\s]+/).map(v => parseFloat(v)).filter(v => !isNaN(v));
        if (values.length === 0) { alert('Enter at least one numeric sweep value.'); return; }
        if (values.length > 30 && !confirm(`${values.length} values → ${values.length} backtests. Proceed?`)) return;
        showLoading(true, `Sweeping ${param} (${values.length} values)…`);
        try {
            const res = await fetch(`${API_BASE}/backtest/sweep`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ base: buildBacktestPayload(), sweep: { param, values } }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${res.status}`);
            }
            renderSweepResults(await res.json());
        } catch (e) {
            alert('Sweep failed: ' + e.message);
        } finally {
            showLoading(false);
        }
    }

    function renderSweepResults(data) {
        const content = document.getElementById('bkt-sweep-results-content');
        if (content) content.style.display = 'block';
        const labelEl = document.getElementById('bkt-sweep-param-label');
        if (labelEl) labelEl.textContent = SWEEP_PARAM_LABELS[data.param] || data.param;

        const container = document.getElementById('bkt-sweep-chart');
        if (container && window.LightweightCharts) {
            container.innerHTML = '';
            const chart = window.LightweightCharts.createChart(container, {
                width: container.clientWidth, height: 220,
                layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#94a3b8' },
                grid: { vertLines: { color: 'rgba(255,255,255,0.05)' }, horzLines: { color: 'rgba(255,255,255,0.05)' } },
                rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
                timeScale: { visible: false },
            });
            const series = chart.addHistogramSeries({ priceFormat: { type: 'percent' } });
            const base = new Date(2020, 0, 1);
            series.setData(data.points.map((p, i) => {
                const d = new Date(base);
                d.setDate(d.getDate() + i);
                const val = p.total_return_pct ?? 0;
                return { time: d.toISOString().split('T')[0], value: val, color: val >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(239,68,68,0.7)' };
            }));
            chart.timeScale().fitContent();
        }

        const tbody = document.querySelector('#bkt-sweep-table tbody');
        if (!tbody) return;
        tbody.innerHTML = '';
        data.points.forEach(p => {
            const tr = document.createElement('tr');
            const ret = p.total_return_pct;
            const dd = p.max_drawdown_pct;
            tr.innerHTML = `
          <td><strong>${p.param_value}</strong></td>
          <td class="${ret != null && ret >= 0 ? 'text-green' : 'text-red'}">${ret != null ? formatPct(ret) : '—'}</td>
          <td>${p.sharpe_ratio != null ? p.sharpe_ratio.toFixed(2) : '—'}</td>
          <td class="text-red">${dd != null ? '-' + formatPct(dd) : '—'}</td>
          <td>${p.trade_count}</td>
          <td>${formatMoney(p.final_equity)}</td>`;
            tbody.appendChild(tr);
        });
    }

    async function saveStrategy() {
        const name = prompt('Strategy name:');
        if (!name) return;
        const definition = buildStrategyPayload();
        try {
            const res = await fetch(`${API_BASE}/backtest/strategies`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, definition }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            await loadSavedStrategiesList();
            alert('Strategy saved.');
        } catch (e) {
            alert('Failed to save: ' + e.message);
        }
    }

    // ── Lifecycle ──────────────────────────────────────────────────────────────

    function render(rootEl) {
        // Reset state for clean re-entry
        conditionIdCounter = 0;
        lastResult = null;
        savedStrategiesById = {};

        rootEl.innerHTML = _htmlTemplate();
        _init();
    }

    function _init() {
        // Dates
        const end = new Date();
        const start = new Date();
        start.setFullYear(start.getFullYear() - 20);
        const endEl = document.getElementById('bkt-end-date');
        const startEl = document.getElementById('bkt-start-date');
        if (endEl) endEl.value = end.toISOString().split('T')[0];
        if (startEl) startEl.value = start.toISOString().split('T')[0];

        // Builder tab switching
        document.querySelectorAll('.bkt-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.bkt-tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                const target = btn.dataset.tab;
                document.querySelectorAll('[data-bkt-panel]').forEach(p => {
                    p.classList.toggle('active', p.dataset.bktPanel === target);
                    p.style.display = p.dataset.bktPanel === target ? 'block' : 'none';
                });
            });
        });

        // Results tab switching
        document.querySelectorAll('.bkt-res-tab').forEach(btn => {
            btn.addEventListener('click', () => switchResultsTab(btn.dataset.tab));
        });

        // Toggle groups (strategy mode, trade view, entry/exit operator)
        document.querySelectorAll('.bkt-toggle-group').forEach(group => {
            group.querySelectorAll('.bkt-toggle-btn').forEach(btn => {
                btn.addEventListener('click', e => {
                    const g = e.target.closest('.bkt-toggle-group');
                    g.querySelectorAll('.bkt-toggle-btn').forEach(b => b.classList.remove('active'));
                    e.target.classList.add('active');
                    if (g.id === 'bkt-trade-view-toggle' && lastResult) renderTradesTable(lastResult.trades || []);
                    if (g.id === 'bkt-strategy-mode-toggle') applyStrategyMode(e.target.dataset.val);
                    updateStrategySummary();
                });
            });
        });

        addCondition('bkt-entry-conditions');
        addCondition('bkt-exit-conditions');
        updateEntryWarning();
        updatePyrFieldVisibility();
        applyStrategyMode('simple');
        selectAsset('stock');

        _inputHandler = updateStrategySummary;
        _changeHandler = updateStrategySummary;
        document.addEventListener('input', _inputHandler);
        document.addEventListener('change', _changeHandler);

        loadSavedStrategiesList();
        updateStrategySummary();
    }

    function teardown() {
        if (_inputHandler) { document.removeEventListener('input', _inputHandler); _inputHandler = null; }
        if (_changeHandler) { document.removeEventListener('change', _changeHandler); _changeHandler = null; }
    }

    window.BacktesterView = {
        render,
        teardown,
        _selectAsset: selectAsset,
        _toggleSizingInputs: toggleSizingInputs,
        _togglePyrInputs: togglePyrInputs,
        _toggleRollEnabled: toggleRollEnabled,
        _updatePyrFieldVisibility: updatePyrFieldVisibility,
        _addCondition: addCondition,
        _removeCondition: removeCondition,
        _updateConditionParams: updateConditionParams,
        _updateConditionType: updateConditionType,
        _run: runBacktest,
        _runWalkForward: runWalkForward,
        _runSweep: runParameterSweep,
        _switchResultsTab: switchResultsTab,
        _applyStrategyMode: applyStrategyMode,
        _loadStrategy: loadSelectedStrategy,
        _deleteStrategy: deleteSelectedStrategy,
        _saveStrategy: saveStrategy,
    };

})();
