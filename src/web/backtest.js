/**
 * Strategy Backtester Frontend Logic
 */

// ── Globals ──────────────────────────────────────────────────────────────────

let apiBase = window.MARKET_INTELLIGENCE_CONFIG?.apiBase || "http://localhost:8000";
let equityChart = null;
let equitySeries = null;
let benchSeries = null;
let wfEquityChart = null;
let wfEquitySeries = null;

let conditionIdCounter = 0;

// ── Initialization ───────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    // Set default dates (20 years back to today)
    const end = new Date();
    const start = new Date();
    start.setFullYear(start.getFullYear() - 20);
    
    document.getElementById("end-date").value = end.toISOString().split('T')[0];
    document.getElementById("start-date").value = start.toISOString().split('T')[0];

    // Toggle operator setup
    const toggleBtns = document.querySelectorAll(".toggle-group .toggle-btn");
    toggleBtns.forEach(btn => {
        btn.addEventListener("click", (e) => {
            const group = e.target.closest('.toggle-group');
            group.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
        });
    });

    // Add first condition rows by default
    addCondition('entry-conditions-container');
    addCondition('exit-conditions-container');

    // Load saved strategies list
    loadSavedStrategiesList();
});

// ── UI Helpers ───────────────────────────────────────────────────────────────

function toggleSizingInputs() {
    const method = document.getElementById("sizing-method").value;
    const valueLabel = document.getElementById("sizing-value-label");
    const valueInput = document.getElementById("sizing-value");
    const riskContainer = document.getElementById("risk-pct-container");

    if (method === "percent_equity") {
        valueLabel.innerText = "Percentage (%)";
        valueInput.value = "100";
        riskContainer.style.display = "none";
    } else if (method === "fixed_dollar") {
        valueLabel.innerText = "Dollar Amount ($)";
        valueInput.value = "10000";
        riskContainer.style.display = "none";
    } else if (method === "fixed_shares") {
        valueLabel.innerText = "Number of Shares";
        valueInput.value = "100";
        riskContainer.style.display = "none";
    } else if (method === "risk_based") {
        valueLabel.innerText = "Stop Distance (N/A)";
        valueInput.value = "0";
        valueInput.disabled = true; // Value is calculated dynamically based on stop
        riskContainer.style.display = "block";
    }
    
    if (method !== "risk_based") {
        valueInput.disabled = false;
    }
}

function toggleWfInputs() {
    const enabled = document.getElementById("wf-enabled").checked;
    const container = document.getElementById("wf-inputs");
    if (enabled) {
        container.style.opacity = "1";
        container.style.pointerEvents = "all";
    } else {
        container.style.opacity = "0.5";
        container.style.pointerEvents = "none";
    }
}

function toggleOptionsInputs() {
    const enabled = document.getElementById("options-enabled").checked;
    const container = document.getElementById("options-inputs");
    if (enabled) {
        container.style.opacity = "1";
        container.style.pointerEvents = "all";
    } else {
        container.style.opacity = "0.5";
        container.style.pointerEvents = "none";
    }
}

function togglePyrInputs() {
    const enabled = document.getElementById("pyr-enabled").checked;
    const container = document.getElementById("pyr-inputs");
    if (enabled) {
        container.style.opacity = "1";
        container.style.pointerEvents = "all";
    } else {
        container.style.opacity = "0.5";
        container.style.pointerEvents = "none";
    }
}

function showLoading(show, msg = "This may take a few seconds.") {
    const overlay = document.getElementById("loading-overlay");
    const msgEl = document.getElementById("loading-msg");
    msgEl.innerText = msg;
    overlay.style.display = show ? "flex" : "none";
}

function formatMoney(val) {
    if (val === null || val === undefined || val === "N/A") return "N/A";
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(val);
}

function formatPct(val) {
    if (val === null || val === undefined || val === "N/A") return "N/A";
    return val.toFixed(2) + "%";
}

function formatNum(val) {
    if (val === null || val === undefined || val === "N/A") return "N/A";
    return val.toString();
}

// ── Condition Builder ────────────────────────────────────────────────────────

function addCondition(containerId) {
    const container = document.getElementById(containerId);
    const id = `cond_${conditionIdCounter++}`;
    
    const div = document.createElement("div");
    div.className = "condition-node builder-row";
    div.id = id;
    
    div.innerHTML = `
        <select class="settings-input cond-indicator" onchange="updateConditionParams('${id}')">
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
            <option value="CONSECUTIVE">Consecutive Candles</option>
        </select>
        
        <span class="cond-params" id="${id}_params">
            <input type="number" class="settings-input param-period" value="14" placeholder="Period" style="width: 70px;">
        </span>
        
        <select class="settings-input cond-type" onchange="updateConditionType('${id}')">
            <option value="threshold">is vs. Value</option>
            <option value="reference">is vs. Indicator</option>
            <option value="crossover">crosses Indicator</option>
        </select>
        
        <span id="${id}_operator">
            <select class="settings-input cond-comparator">
                <option value="lt">Less Than (<)</option>
                <option value="gt">Greater Than (>)</option>
                <option value="lte">Less or Equal (<=)</option>
                <option value="gte">Greater or Equal (>=)</option>
            </select>
        </span>
        
        <span class="cond-target" id="${id}_target">
            <input type="number" class="settings-input target-value" value="30" style="width: 80px;">
        </span>
        
        <button class="btn icon danger" onclick="document.getElementById('${id}').remove()" style="padding: 0.5rem; margin-left: auto;">✕</button>
    `;
    
    container.appendChild(div);
}

function updateConditionParams(rowId) {
    const row = document.getElementById(rowId);
    const ind = row.querySelector(".cond-indicator").value;
    const paramsContainer = document.getElementById(`${rowId}_params`);
    const typeSelect = row.querySelector(".cond-type");
    
    // Default visibility
    typeSelect.style.display = "block";
    document.getElementById(`${rowId}_operator`).style.display = "block";
    document.getElementById(`${rowId}_target`).style.display = "block";
    
    if (ind === "PRICE" || ind === "VWAP") {
        paramsContainer.innerHTML = "";
    } else if (ind === "MACD_LINE" || ind === "MACD_HISTOGRAM") {
        paramsContainer.innerHTML = `
            <input type="number" class="settings-input param-fast" value="12" style="width: 50px;" title="Fast">
            <input type="number" class="settings-input param-slow" value="26" style="width: 50px;" title="Slow">
        `;
    } else if (ind.startsWith("BB_")) {
        paramsContainer.innerHTML = `
            <input type="number" class="settings-input param-period" value="20" placeholder="Period" style="width: 60px;">
            <input type="number" class="settings-input param-stddev" value="2.0" step="0.1" placeholder="StdDev" style="width: 60px;">
        `;
    } else if (ind === "PULLBACK") {
        paramsContainer.innerHTML = ``;
        typeSelect.style.display = "none";
        document.getElementById(`${rowId}_operator`).style.display = "none";
        document.getElementById(`${rowId}_target`).innerHTML = `
            Drop <input type="number" class="settings-input pb-pct" value="5.0" style="width: 60px;"> % 
            from <input type="number" class="settings-input pb-lookback" value="20" style="width: 60px;"> day high
        `;
    } else if (ind === "CONSECUTIVE") {
        paramsContainer.innerHTML = ``;
        typeSelect.style.display = "none";
        document.getElementById(`${rowId}_operator`).style.display = "none";
        document.getElementById(`${rowId}_target`).innerHTML = `
            <input type="number" class="settings-input cons-count" value="3" style="width: 60px;">
            <select class="settings-input cons-dir">
                <option value="down">Red</option>
                <option value="up">Green</option>
            </select>
            candles
        `;
    } else {
        paramsContainer.innerHTML = `
            <input type="number" class="settings-input param-period" value="14" placeholder="Period" style="width: 70px;">
        `;
    }
}

function updateConditionType(rowId) {
    const row = document.getElementById(rowId);
    const type = row.querySelector(".cond-type").value;
    const operatorContainer = document.getElementById(`${rowId}_operator`);
    const targetContainer = document.getElementById(`${rowId}_target`);
    
    if (type === "threshold") {
        operatorContainer.innerHTML = `
            <select class="settings-input cond-comparator">
                <option value="lt">Less Than (<)</option>
                <option value="gt">Greater Than (>)</option>
                <option value="lte">Less or Equal (<=)</option>
                <option value="gte">Greater or Equal (>=)</option>
            </select>
        `;
        targetContainer.innerHTML = `
            <input type="number" class="settings-input target-value" value="30" style="width: 80px;">
        `;
    } else if (type === "reference") {
        operatorContainer.innerHTML = `
            <select class="settings-input cond-comparator">
                <option value="lt">Less Than (<)</option>
                <option value="gt">Greater Than (>)</option>
            </select>
        `;
        targetContainer.innerHTML = `
            <select class="settings-input target-indicator">
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
                <option value="VWAP">VWAP</option>
                <option value="BB_UPPER">BB Upper</option>
                <option value="BB_MIDDLE">BB Middle</option>
                <option value="BB_LOWER">BB Lower</option>
            </select>
            <input type="number" class="settings-input target-period" value="200" style="width: 70px;">
        `;
    } else if (type === "crossover") {
        operatorContainer.innerHTML = `
            <select class="settings-input cond-direction">
                <option value="above">Above</option>
                <option value="below">Below</option>
            </select>
        `;
        targetContainer.innerHTML = `
            <select class="settings-input target-indicator">
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
                <option value="VWAP">VWAP</option>
                <option value="BB_UPPER">BB Upper</option>
                <option value="BB_MIDDLE">BB Middle</option>
                <option value="BB_LOWER">BB Lower</option>
                <option value="MACD_SIGNAL">MACD Signal</option>
            </select>
            <input type="number" class="settings-input target-period" value="200" style="width: 70px;">
        `;
    }
}

function serializeConditions(containerId, operatorId) {
    const container = document.getElementById(containerId);
    const rows = container.querySelectorAll(".condition-node");
    const conditions = [];
    
    rows.forEach(row => {
        const ind = row.querySelector(".cond-indicator").value;
        
        if (ind === "PULLBACK") {
            conditions.push({
                type: "pullback",
                lookback: parseInt(row.querySelector(".pb-lookback").value),
                pct: parseFloat(row.querySelector(".pb-pct").value)
            });
            return;
        }
        
        if (ind === "CONSECUTIVE") {
            conditions.push({
                type: "consecutive",
                count: parseInt(row.querySelector(".cons-count").value),
                direction: row.querySelector(".cons-dir").value
            });
            return;
        }
        
        // Standard indicator parsing
        const params = {};
        if (ind.startsWith("BB_")) {
            params.period = parseInt(row.querySelector(".param-period").value);
            params.std_dev = parseFloat(row.querySelector(".param-stddev").value);
        } else if (row.querySelector(".param-period")) {
            params.period = parseInt(row.querySelector(".param-period").value);
        } else if (row.querySelector(".param-fast")) {
            params.fast = parseInt(row.querySelector(".param-fast").value);
            params.slow = parseInt(row.querySelector(".param-slow").value);
        }
        
        const type = row.querySelector(".cond-type").value;
        const baseInd = { name: ind, params: params };
        
        if (type === "threshold") {
            conditions.push({
                type: "threshold",
                indicator: baseInd,
                comparator: row.querySelector(".cond-comparator").value,
                value: parseFloat(row.querySelector(".target-value").value)
            });
        } else if (type === "reference") {
            const refIndName = row.querySelector(".target-indicator").value;
            const refPeriod = parseInt(row.querySelector(".target-period")?.value || 0);
            const refInd = { name: refIndName, params: refPeriod ? { period: refPeriod } : {} };
            
            conditions.push({
                type: "reference",
                indicator: baseInd,
                comparator: row.querySelector(".cond-comparator").value,
                ref_indicator: refInd
            });
        } else if (type === "crossover") {
            const refIndName = row.querySelector(".target-indicator").value;
            const refPeriod = parseInt(row.querySelector(".target-period")?.value || 0);
            const refInd = { name: refIndName, params: refPeriod ? { period: refPeriod } : {} };
            
            conditions.push({
                type: "crossover",
                indicator: baseInd,
                direction: row.querySelector(".cond-direction").value,
                ref_indicator: refInd
            });
        }
    });
    
    if (conditions.length === 0) return null;
    
    const operator = document.querySelector(`#${operatorId} .active`).dataset.val;
    
    return {
        operator: operator,
        conditions: conditions
    };
}

function buildStrategyPayload() {
    const entryTree = serializeConditions("entry-conditions-container", "entry-operator-toggle");
    const exitTree = serializeConditions("exit-conditions-container", "exit-operator-toggle");
    
    // We need at least an empty node if they deleted all conditions, or just pass null
    const strategy = {
        name: "UI Strategy",
        entry: entryTree || { operator: "AND", conditions: [] },
        exit: {
            conditions: exitTree,
            stop_loss_pct: parseFloat(document.getElementById("stop-loss").value) || null,
            take_profit_pct: parseFloat(document.getElementById("take-profit").value) || null,
            trailing_stop_pct: parseFloat(document.getElementById("trailing-stop").value) || null,
            max_hold_days: parseInt(document.getElementById("max-hold").value) || null,
            pyramiding_exit_mode: document.getElementById("pyr-exit") ? document.getElementById("pyr-exit").value : "sell_all"
        },
        position_sizing: {
            method: document.getElementById("sizing-method").value,
            value: parseFloat(document.getElementById("sizing-value").value) || 100,
            risk_pct: parseFloat(document.getElementById("risk-pct").value) || null
        },
        options: {
            enabled: document.getElementById("options-enabled").checked,
            type: document.getElementById("option-type").value,
            target_dte: parseInt(document.getElementById("option-dte").value) || 30,
            target_delta: parseFloat(document.getElementById("option-delta").value) || 0.50
        },
        pyramiding: {
            enabled: document.getElementById("pyr-enabled").checked,
            max_positions: parseInt(document.getElementById("pyr-max").value) || 3,
            scale_in_trigger: document.getElementById("pyr-trigger").value,
            scale_in_value: parseFloat(document.getElementById("pyr-value").value) || null
        },
        direction: "long"
    };
    
    return strategy;
}

// ── Execution ────────────────────────────────────────────────────────────────

async function runBacktest() {
    const isWf = document.getElementById("wf-enabled").checked;
    
    const payload = {
        strategy: buildStrategyPayload(),
        ticker: document.getElementById("ticker").value,
        start_date: document.getElementById("start-date").value,
        end_date: document.getElementById("end-date").value,
        timeframe: document.getElementById("timeframe").value,
        initial_capital: parseFloat(document.getElementById("capital").value) || 10000,
        commission: 0.0,
        slippage_pct: 0.0
    };
    
    showLoading(true, `Crunching historical data for ${payload.ticker}...`);
    
    try {
        let endpoint = `${apiBase}/backtest/run`;
        if (isWf) {
            endpoint = `${apiBase}/backtest/walk-forward`;
            payload.mode = document.getElementById("wf-mode").value;
            payload.in_sample_days = parseInt(document.getElementById("wf-is-days").value);
            payload.out_of_sample_days = parseInt(document.getElementById("wf-oos-days").value);
        }
        
        const res = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "Request failed");
        }
        
        const data = await res.json();
        
        document.getElementById("results-panel").style.display = "block";
        
        if (isWf) {
            renderWalkForwardResults(data);
        } else {
            document.getElementById("wf-results-section").style.display = "none";
            renderStandardResults(data);
        }
        
    } catch (e) {
        alert("Backtest failed: " + e.message);
    } finally {
        showLoading(false);
    }
}

// ── Rendering ────────────────────────────────────────────────────────────────

function renderStandardResults(data) {
    document.getElementById("results-title").innerText = `Results: ${data.ticker}`;
    document.getElementById("results-period").innerText = `${data.start_date} → ${data.end_date}`;
    
    renderStats(data.stats, "stats-container");
    renderEquityCurve(data.equity_curve, data.benchmark_curve, "equity-chart");
    renderTradesTable(data.trades);
}

function renderWalkForwardResults(data) {
    document.getElementById("wf-results-section").style.display = "block";
    document.getElementById("wf-mode-badge").innerText = data.mode.toUpperCase();
    
    // We also render the standard results section, but feed it the aggregated OOS data
    document.getElementById("results-title").innerText = `Aggregated OOS Results: ${data.ticker}`;
    document.getElementById("results-period").innerText = `${data.start_date} → ${data.end_date}`;
    
    // Render WF specific sections
    renderStats(data.aggregated_oos_stats, "wf-stats-container");
    renderEquityCurve(data.aggregated_oos_curve, null, "wf-equity-chart", true);
    
    const tbody = document.querySelector("#wf-fold-table tbody");
    tbody.innerHTML = "";
    
    data.folds.forEach(f => {
        const tr = document.createElement("tr");
        
        const deg = data.degradation_ratios.sharpe_ratio;
        let degStr = "N/A";
        if (deg !== null) {
            degStr = deg.toFixed(2);
        }
        
        tr.innerHTML = `
            <td>Fold ${f.fold_number}</td>
            <td>${f.is_start} → ${f.is_end}</td>
            <td>${f.oos_start} → ${f.oos_end}</td>
            <td><span class="badge ${f.regime === 'bull' ? 'green' : f.regime === 'bear' ? 'danger' : 'blue'}">${f.regime.toUpperCase()}</span></td>
            <td class="${f.is_stats.total_return_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(f.is_stats.total_return_pct)}</td>
            <td class="${f.oos_stats.total_return_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(f.oos_stats.total_return_pct)}</td>
            <td>${degStr}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderStats(stats, containerId) {
    const container = document.getElementById(containerId);
    container.innerHTML = `
        <div class="stat-card">
            <div class="stat-label">Total Return</div>
            <div class="stat-val ${stats.total_return_pct >= 0 ? 'text-green' : 'text-red'}">${formatPct(stats.total_return_pct)}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">CAGR</div>
            <div class="stat-val ${stats.cagr >= 0 ? 'text-green' : 'text-red'}">${stats.cagr === 'N/A' ? 'N/A' : formatPct(stats.cagr)}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Sharpe Ratio</div>
            <div class="stat-val ${stats.sharpe_ratio >= 1.0 ? 'text-green' : ''}">${stats.sharpe_ratio}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Max Drawdown</div>
            <div class="stat-val text-red">-${formatPct(stats.max_drawdown_pct)}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Win Rate</div>
            <div class="stat-val ${stats.win_rate_pct >= 50 ? 'text-green' : 'text-red'}">${formatPct(stats.win_rate_pct)}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Total Trades</div>
            <div class="stat-val">${stats.total_trades}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Profit Factor</div>
            <div class="stat-val">${stats.profit_factor}</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Alpha (vs B&H)</div>
            <div class="stat-val ${stats.alpha >= 0 ? 'text-green' : 'text-red'}">${stats.alpha === 'N/A' ? 'N/A' : formatPct(stats.alpha)}</div>
        </div>
    `;
}

function renderTradesTable(trades) {
    const tbody = document.querySelector("#trades-table tbody");
    tbody.innerHTML = "";
    
    // Sort reverse chronological
    const sorted = [...trades].reverse();
    
    sorted.forEach(t => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>${t.entry_date}</td>
            <td>${t.exit_date}</td>
            <td>${t.direction.toUpperCase()}</td>
            <td>${formatMoney(t.entry_price)}</td>
            <td>${formatMoney(t.exit_price)}</td>
            <td>${t.shares}</td>
            <td class="${t.pnl_pct >= 0 ? 'text-green' : 'text-red'}"><strong>${formatPct(t.pnl_pct)}</strong></td>
            <td class="${t.pnl >= 0 ? 'text-green' : 'text-red'}">${formatMoney(t.pnl)}</td>
            <td><span class="badge blue">${t.exit_reason.replace('_', ' ')}</span></td>
        `;
        tbody.appendChild(tr);
    });
}

function renderEquityCurve(curveData, benchData, containerId, isWf = false) {
    const container = document.getElementById(containerId);
    container.innerHTML = ""; // clear previous
    
    const chartParams = {
        width: container.clientWidth,
        height: 400,
        layout: {
            background: { type: 'solid', color: 'transparent' },
            textColor: '#94a3b8',
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
        },
        timeScale: {
            timeVisible: false,
            borderColor: 'rgba(255, 255, 255, 0.1)',
        },
        rightPriceScale: {
            borderColor: 'rgba(255, 255, 255, 0.1)',
        }
    };
    
    const chart = LightweightCharts.createChart(container, chartParams);
    
    const eqSeries = chart.addAreaSeries({
        lineColor: '#3b82f6',
        topColor: 'rgba(59, 130, 246, 0.4)',
        bottomColor: 'rgba(59, 130, 246, 0.0)',
        lineWidth: 2,
    });
    
    const formattedEq = curveData.map(d => ({
        time: d.date,
        value: d.equity
    }));
    
    eqSeries.setData(formattedEq);
    
    if (benchData) {
        const bSeries = chart.addLineSeries({
            color: '#94a3b8',
            lineWidth: 1,
            lineStyle: 2, // dashed
        });
        
        const formattedBench = benchData.map(d => ({
            time: d.date,
            value: d.equity
        }));
        bSeries.setData(formattedBench);
    }
    
    chart.timeScale().fitContent();
    
    // Handle resize
    window.addEventListener('resize', () => {
        chart.applyOptions({ width: container.clientWidth });
    });
}

// ── Saved Strategies ─────────────────────────────────────────────────────────

async function loadSavedStrategiesList() {
    try {
        const res = await fetch(`${apiBase}/backtest/strategies`);
        if (!res.ok) return;
        const data = await res.json();
        
        const select = document.getElementById("saved-strategies");
        select.innerHTML = '<option value="">Load Strategy...</option>';
        
        // TODO: Map data.strategies to options and handle load
    } catch (e) {
        console.error("Failed to load strategies", e);
    }
}

async function saveStrategy() {
    const name = prompt("Enter a name for this strategy:");
    if (!name) return;
    
    const definition = buildStrategyPayload();
    
    try {
        const res = await fetch(`${apiBase}/backtest/strategies`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name, definition })
        });
        
        if (res.ok) {
            alert("Strategy saved successfully.");
            loadSavedStrategiesList();
        }
    } catch (e) {
        alert("Failed to save strategy: " + e.message);
    }
}
