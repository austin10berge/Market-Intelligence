/**
 * Strategy Backtester Frontend Logic
 *
 * Key responsibilities:
 *   • Serialize the form into the BacktestRequest schema the API expects.
 *   • Render results: stats grid, equity-curve chart (with avg-cost-basis
 *     overlay when scale-in is active), trade table (Tranches vs Campaigns).
 *   • Saved-strategy CRUD (load, save, delete).
 */

// ── Globals ──────────────────────────────────────────────────────────────────

const apiBase = window.MARKET_INTELLIGENCE_CONFIG?.apiBase || "http://localhost:8000";
let equityChart = null;
let conditionIdCounter = 0;
let lastResult = null;
let lastIsWalkForward = false;
let savedStrategiesIndex = {};   // id → name
let savedStrategiesById = {};    // id → full {id, name, definition}

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    const end = new Date();
    const start = new Date();
    start.setFullYear(start.getFullYear() - 20);
    document.getElementById("end-date").value = end.toISOString().split("T")[0];
    document.getElementById("start-date").value = start.toISOString().split("T")[0];

    // Wire up the AND/OR + Tranches/Campaigns toggle groups
    document.querySelectorAll(".toggle-group .toggle-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const group = e.target.closest(".toggle-group");
            group.querySelectorAll(".toggle-btn").forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            // Re-render trade table when the user flips the view
            if (group.id === "trade-view-toggle" && lastResult) {
                renderTradesTable(getActiveTradesData());
            }
        });
    });

    addCondition("entry-conditions-container");
    addCondition("exit-conditions-container");
    updateEntryWarning();
    updatePyrFieldVisibility();

    loadSavedStrategiesList();
});

// ── UI toggle helpers ────────────────────────────────────────────────────────

function setSectionEnabled(containerId, enabled) {
    const el = document.getElementById(containerId);
    el.style.opacity = enabled ? "1" : "0.5";
    el.style.pointerEvents = enabled ? "all" : "none";
}

function toggleSizingInputs() {
    const method = document.getElementById("sizing-method").value;
    const valueLabel = document.getElementById("sizing-value-label");
    const valueInput = document.getElementById("sizing-value");
    const riskContainer = document.getElementById("risk-pct-container");

    if (method === "percent_equity") {
        valueLabel.innerText = "Percentage (%)";
        if (!valueInput.value || valueInput.disabled) valueInput.value = "100";
        riskContainer.style.display = "none";
    } else if (method === "fixed_dollar") {
        valueLabel.innerText = "Dollar Amount ($)";
        if (!valueInput.value || valueInput.disabled) valueInput.value = "10000";
        riskContainer.style.display = "none";
    } else if (method === "fixed_shares") {
        valueLabel.innerText = "Number of Shares";
        if (!valueInput.value || valueInput.disabled) valueInput.value = "100";
        riskContainer.style.display = "none";
    } else if (method === "risk_based") {
        valueLabel.innerText = "Stop Distance (N/A)";
        valueInput.value = "0";
        valueInput.disabled = true;
        riskContainer.style.display = "block";
    }
    if (method !== "risk_based") valueInput.disabled = false;
}

function toggleWfInputs() { setSectionEnabled("wf-inputs", document.getElementById("wf-enabled").checked); }
function toggleOptionsInputs() { setSectionEnabled("options-inputs", document.getElementById("options-enabled").checked); }
function togglePyrInputs() { setSectionEnabled("pyr-inputs", document.getElementById("pyr-enabled").checked); }

function updatePyrFieldVisibility() {
    const mode = document.getElementById("pyr-trigger-mode").value;
    const pullbackPctEl = document.getElementById("pyr-pullback-pct-container");
    const pullbackRefEl = document.getElementById("pyr-pullback-ref-container");
    const showPullback = mode === "pullback_only" || mode === "both";
    pullbackPctEl.style.display = showPullback ? "block" : "none";
    pullbackRefEl.style.display = showPullback ? "block" : "none";
}

function showLoading(show, msg = "This may take a few seconds.") {
    document.getElementById("loading-msg").innerText = msg;
    document.getElementById("loading-overlay").style.display = show ? "flex" : "none";
}

// ── Formatting helpers ───────────────────────────────────────────────────────

function formatMoney(val) {
    if (val === null || val === undefined || val === "N/A") return "N/A";
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(val);
}
function formatPct(val) {
    if (val === null || val === undefined || val === "N/A") return "N/A";
    return Number(val).toFixed(2) + "%";
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
                <option value="lt">Less Than (&lt;)</option>
                <option value="gt">Greater Than (&gt;)</option>
                <option value="lte">Less or Equal (&lt;=)</option>
                <option value="gte">Greater or Equal (&gt;=)</option>
            </select>
        </span>

        <span class="cond-target" id="${id}_target">
            <input type="number" class="settings-input target-value" value="30" style="width: 80px;">
        </span>

        <button class="btn icon danger" onclick="removeCondition('${id}')" style="padding: 0.5rem; margin-left: auto;">✕</button>
    `;
    container.appendChild(div);
    updateEntryWarning();
}

function removeCondition(rowId) {
    document.getElementById(rowId)?.remove();
    updateEntryWarning();
}

function updateEntryWarning() {
    const warning = document.getElementById("entry-warning");
    if (!warning) return;
    const has = document.getElementById("entry-conditions-container").querySelectorAll(".condition-node").length > 0;
    warning.style.display = has ? "none" : "block";
}

function updateConditionParams(rowId) {
    const row = document.getElementById(rowId);
    const ind = row.querySelector(".cond-indicator").value;
    const paramsContainer = document.getElementById(`${rowId}_params`);
    const typeSelect = row.querySelector(".cond-type");

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
        paramsContainer.innerHTML = "";
        typeSelect.style.display = "none";
        document.getElementById(`${rowId}_operator`).style.display = "none";
        document.getElementById(`${rowId}_target`).innerHTML = `
            Drop <input type="number" class="settings-input pb-pct" value="5.0" style="width: 60px;"> %
            from <input type="number" class="settings-input pb-lookback" value="20" style="width: 60px;"> day high
        `;
    } else if (ind === "CONSECUTIVE") {
        paramsContainer.innerHTML = "";
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
    const op = document.getElementById(`${rowId}_operator`);
    const tgt = document.getElementById(`${rowId}_target`);

    if (type === "threshold") {
        op.innerHTML = `
            <select class="settings-input cond-comparator">
                <option value="lt">Less Than (&lt;)</option>
                <option value="gt">Greater Than (&gt;)</option>
                <option value="lte">Less or Equal (&lt;=)</option>
                <option value="gte">Greater or Equal (&gt;=)</option>
            </select>`;
        tgt.innerHTML = `<input type="number" class="settings-input target-value" value="30" style="width: 80px;">`;
    } else if (type === "reference") {
        op.innerHTML = `
            <select class="settings-input cond-comparator">
                <option value="lt">Less Than (&lt;)</option>
                <option value="gt">Greater Than (&gt;)</option>
            </select>`;
        tgt.innerHTML = `
            <select class="settings-input target-indicator">
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
                <option value="VWAP">VWAP</option>
                <option value="BB_UPPER">BB Upper</option>
                <option value="BB_MIDDLE">BB Middle</option>
                <option value="BB_LOWER">BB Lower</option>
            </select>
            <input type="number" class="settings-input target-period" value="200" style="width: 70px;">`;
    } else if (type === "crossover") {
        op.innerHTML = `
            <select class="settings-input cond-direction">
                <option value="above">Above</option>
                <option value="below">Below</option>
            </select>`;
        tgt.innerHTML = `
            <select class="settings-input target-indicator">
                <option value="SMA">SMA</option>
                <option value="EMA">EMA</option>
                <option value="VWAP">VWAP</option>
                <option value="BB_UPPER">BB Upper</option>
                <option value="BB_MIDDLE">BB Middle</option>
                <option value="BB_LOWER">BB Lower</option>
                <option value="MACD_SIGNAL">MACD Signal</option>
            </select>
            <input type="number" class="settings-input target-period" value="200" style="width: 70px;">`;
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
            conditions.push({
                type: "reference",
                indicator: baseInd,
                comparator: row.querySelector(".cond-comparator").value,
                ref_indicator: { name: refIndName, params: refPeriod ? { period: refPeriod } : {} }
            });
        } else if (type === "crossover") {
            const refIndName = row.querySelector(".target-indicator").value;
            const refPeriod = parseInt(row.querySelector(".target-period")?.value || 0);
            conditions.push({
                type: "crossover",
                indicator: baseInd,
                direction: row.querySelector(".cond-direction").value,
                ref_indicator: { name: refIndName, params: refPeriod ? { period: refPeriod } : {} }
            });
        }
    });

    if (conditions.length === 0) return null;
    const operator = document.querySelector(`#${operatorId} .active`).dataset.val;
    return { operator, conditions };
}

function buildStrategyPayload() {
    const entryTree = serializeConditions("entry-conditions-container", "entry-operator-toggle");
    const exitTree = serializeConditions("exit-conditions-container", "exit-operator-toggle");

    return {
        name: "UI Strategy",
        entry: entryTree || { operator: "AND", conditions: [] },
        exit: {
            conditions: exitTree,
            stop_loss_pct: parseFloat(document.getElementById("stop-loss").value) || null,
            take_profit_pct: parseFloat(document.getElementById("take-profit").value) || null,
            trailing_stop_pct: parseFloat(document.getElementById("trailing-stop").value) || null,
            max_hold_days: parseInt(document.getElementById("max-hold").value) || null,
            pyramiding_exit_mode: document.getElementById("pyr-exit").value
        },
        position_sizing: {
            method: document.getElementById("sizing-method").value,
            value: parseFloat(document.getElementById("sizing-value").value) || 100,
            risk_pct: parseFloat(document.getElementById("risk-pct").value) || null
        },
        options: {
            enabled: document.getElementById("options-enabled").checked,
            type: document.getElementById("option-type").value,
            position: document.getElementById("option-position").value,
            target_dte: parseInt(document.getElementById("option-dte").value) || 30,
            target_delta: parseFloat(document.getElementById("option-delta").value) || 0.50
        },
        pyramiding: {
            enabled: document.getElementById("pyr-enabled").checked,
            max_positions: parseInt(document.getElementById("pyr-max").value) || 3,
            trigger_mode: document.getElementById("pyr-trigger-mode").value,
            pullback_pct: parseFloat(document.getElementById("pyr-pullback-pct").value) || null,
            pullback_reference: document.getElementById("pyr-pullback-ref").value
        }
    };
}

// ── Execution ────────────────────────────────────────────────────────────────

async function runBacktest() {
    if (document.getElementById("entry-conditions-container").querySelectorAll(".condition-node").length === 0) {
        alert("Please add at least one entry condition.");
        return;
    }

    const isWf = document.getElementById("wf-enabled").checked;

    const payload = {
        strategy: buildStrategyPayload(),
        ticker: document.getElementById("ticker").value.trim().toUpperCase(),
        start_date: document.getElementById("start-date").value,
        end_date: document.getElementById("end-date").value,
        timeframe: document.getElementById("timeframe").value,
        initial_capital: parseFloat(document.getElementById("capital").value) || 10000,
        commission: parseFloat(document.getElementById("commission").value) || 0.0,
        slippage_pct: parseFloat(document.getElementById("slippage").value) || 0.0
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
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }

        const data = await res.json();
        lastResult = data;
        lastIsWalkForward = isWf;
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
    document.getElementById("results-title").innerText = `Aggregated OOS Results: ${data.ticker}`;
    document.getElementById("results-period").innerText = `${data.start_date} → ${data.end_date}`;

    renderStats(data.aggregated_oos_stats, "wf-stats-container");
    renderEquityCurve(data.aggregated_oos_curve, null, "wf-equity-chart");

    const tbody = document.querySelector("#wf-fold-table tbody");
    tbody.innerHTML = "";
    data.folds.forEach(f => {
        const deg = data.degradation_ratios?.sharpe_ratio;
        const degStr = deg !== null && deg !== undefined ? Number(deg).toFixed(2) : "N/A";
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>Fold ${f.fold_number}</td>
            <td>${f.is_start} → ${f.is_end}</td>
            <td>${f.oos_start} → ${f.oos_end}</td>
            <td><span class="badge ${f.regime === "bull" ? "green" : f.regime === "bear" ? "danger" : "blue"}">${f.regime.toUpperCase()}</span></td>
            <td class="${f.is_stats.total_return_pct >= 0 ? "text-green" : "text-red"}">${formatPct(f.is_stats.total_return_pct)}</td>
            <td class="${f.oos_stats.total_return_pct >= 0 ? "text-green" : "text-red"}">${formatPct(f.oos_stats.total_return_pct)}</td>
            <td>${degStr}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderStats(stats, containerId) {
    const container = document.getElementById(containerId);
    container.innerHTML = `
        <div class="stat-card"><div class="stat-label">Total Return</div><div class="stat-val ${stats.total_return_pct >= 0 ? "text-green" : "text-red"}">${formatPct(stats.total_return_pct)}</div></div>
        <div class="stat-card"><div class="stat-label">CAGR</div><div class="stat-val ${stats.cagr === "N/A" ? "" : stats.cagr >= 0 ? "text-green" : "text-red"}">${stats.cagr === "N/A" ? "N/A" : formatPct(stats.cagr)}</div></div>
        <div class="stat-card"><div class="stat-label">Sharpe Ratio</div><div class="stat-val ${stats.sharpe_ratio >= 1.0 ? "text-green" : ""}">${stats.sharpe_ratio}</div></div>
        <div class="stat-card"><div class="stat-label">Sortino</div><div class="stat-val">${stats.sortino_ratio}</div></div>
        <div class="stat-card"><div class="stat-label">Max Drawdown</div><div class="stat-val text-red">-${formatPct(stats.max_drawdown_pct)}</div></div>
        <div class="stat-card"><div class="stat-label">Win Rate</div><div class="stat-val ${stats.win_rate_pct >= 50 ? "text-green" : "text-red"}">${formatPct(stats.win_rate_pct)}</div></div>
        <div class="stat-card"><div class="stat-label">Total Trades</div><div class="stat-val">${stats.total_trades}</div></div>
        <div class="stat-card"><div class="stat-label">Profit Factor</div><div class="stat-val">${stats.profit_factor}</div></div>
        <div class="stat-card"><div class="stat-label">Alpha (vs B&H)</div><div class="stat-val ${stats.alpha === "N/A" ? "" : stats.alpha >= 0 ? "text-green" : "text-red"}">${stats.alpha === "N/A" ? "N/A" : formatPct(stats.alpha)}</div></div>
    `;
}

// ── Trade table: Tranches vs Campaigns ────────────────────────────────────────

function getActiveTradeView() {
    const btn = document.querySelector("#trade-view-toggle .active");
    return btn ? btn.dataset.val : "tranches";
}

function getActiveTradesData() {
    return lastResult?.trades || [];
}

function groupTradesByCampaign(trades) {
    // Aggregate tranches that share a campaign_id into one summary row
    const groups = new Map();
    trades.forEach(t => {
        const key = t.campaign_id || 0;
        if (!groups.has(key)) {
            groups.set(key, {
                campaign_id: key,
                entry_date: t.entry_date,
                exit_date: t.exit_date,
                tranches: 0,
                contracts_or_shares: 0,
                cost_basis: 0,
                proceeds: 0,
                pnl: 0,
                is_option: t.is_option,
                option_type: t.option_type,
                option_position: t.option_position,
                exit_reasons: new Set(),
                direction: t.direction,
            });
        }
        const g = groups.get(key);
        g.tranches += 1;
        const multiplier = t.is_option ? 100 : 1;
        g.contracts_or_shares += t.shares;
        g.cost_basis += t.shares * t.entry_price * multiplier;
        g.proceeds += t.shares * t.exit_price * multiplier;
        g.pnl += t.pnl;
        g.entry_date = g.entry_date < t.entry_date ? g.entry_date : t.entry_date;
        g.exit_date = g.exit_date > t.exit_date ? g.exit_date : t.exit_date;
        g.exit_reasons.add(t.exit_reason);
    });
    const rows = [...groups.values()];
    rows.forEach(g => {
        g.avg_entry = g.cost_basis / g.contracts_or_shares / (g.is_option ? 100 : 1);
        g.avg_exit = g.proceeds / g.contracts_or_shares / (g.is_option ? 100 : 1);
        g.pnl_pct = g.cost_basis > 0 ? (g.pnl / g.cost_basis) * 100 : 0;
    });
    return rows.sort((a, b) => b.entry_date.localeCompare(a.entry_date));
}

function renderTradesTable(trades) {
    const tbody = document.querySelector("#trades-table tbody");
    const thead = document.getElementById("trades-table-head");
    tbody.innerHTML = "";

    const hasOptions = trades.some(t => t.is_option);
    const view = getActiveTradeView();

    if (view === "campaigns") {
        thead.innerHTML = `
            <th>First Entry</th><th>Last Exit</th><th>Tranches</th>
            <th>${hasOptions ? "Contracts" : "Shares"}</th>
            <th>Avg Entry</th><th>Avg Exit</th>
            <th>P&L %</th><th>P&L $</th><th>Exit Reasons</th>
        `;
        const groups = groupTradesByCampaign(trades);
        groups.forEach(g => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${g.entry_date}</td>
                <td>${g.exit_date}</td>
                <td>${g.tranches}</td>
                <td>${g.contracts_or_shares}</td>
                <td>${formatMoney(g.avg_entry)}</td>
                <td>${formatMoney(g.avg_exit)}</td>
                <td class="${g.pnl_pct >= 0 ? "text-green" : "text-red"}"><strong>${formatPct(g.pnl_pct)}</strong></td>
                <td class="${g.pnl >= 0 ? "text-green" : "text-red"}">${formatMoney(g.pnl)}</td>
                <td>${[...g.exit_reasons].map(r => `<span class="badge blue">${r.replace(/_/g, " ")}</span>`).join(" ")}</td>
            `;
            tbody.appendChild(tr);
        });
        return;
    }

    // Tranche view
    if (hasOptions) {
        thead.innerHTML = `
            <th>Entry</th><th>Exit</th><th>Side</th>
            <th>Type/Strike</th><th>Contracts</th>
            <th>Entry Prem.</th><th>Exit Prem.</th>
            <th>Cost ($)</th><th>P&L %</th><th>P&L $</th><th>Reason</th>
        `;
    } else {
        thead.innerHTML = `
            <th>Entry</th><th>Exit</th><th>Dir</th>
            <th>Entry $</th><th>Exit $</th><th>Shares</th>
            <th>P&L %</th><th>P&L $</th><th>Reason</th>
        `;
    }

    const sorted = [...trades].reverse();
    sorted.forEach(t => {
        const tr = document.createElement("tr");
        if (t.is_option) {
            const sideLabel = (t.option_position || "long").toUpperCase() + " " + (t.option_type || "").toUpperCase();
            const cost = (t.shares * t.entry_price * 100).toFixed(2);
            tr.innerHTML = `
                <td>${t.entry_date}</td>
                <td>${t.exit_date}</td>
                <td>${sideLabel}</td>
                <td>${t.option_type ? t.option_type.toUpperCase()[0] : ""} ${t.option_strike ?? ""}</td>
                <td>${t.shares}</td>
                <td>${formatMoney(t.entry_price)}</td>
                <td>${formatMoney(t.exit_price)}</td>
                <td>${formatMoney(cost)}</td>
                <td class="${t.pnl_pct >= 0 ? "text-green" : "text-red"}"><strong>${formatPct(t.pnl_pct)}</strong></td>
                <td class="${t.pnl >= 0 ? "text-green" : "text-red"}">${formatMoney(t.pnl)}</td>
                <td><span class="badge blue">${t.exit_reason.replace(/_/g, " ")}</span></td>
            `;
        } else {
            tr.innerHTML = `
                <td>${t.entry_date}</td>
                <td>${t.exit_date}</td>
                <td>${t.direction.toUpperCase()}</td>
                <td>${formatMoney(t.entry_price)}</td>
                <td>${formatMoney(t.exit_price)}</td>
                <td>${t.shares}</td>
                <td class="${t.pnl_pct >= 0 ? "text-green" : "text-red"}"><strong>${formatPct(t.pnl_pct)}</strong></td>
                <td class="${t.pnl >= 0 ? "text-green" : "text-red"}">${formatMoney(t.pnl)}</td>
                <td><span class="badge blue">${t.exit_reason.replace(/_/g, " ")}</span></td>
            `;
        }
        tbody.appendChild(tr);
    });
}

// ── Equity curve (with optional avg-cost overlay) ────────────────────────────

function renderEquityCurve(curveData, benchData, containerId) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    const chart = LightweightCharts.createChart(container, {
        width: container.clientWidth,
        height: 400,
        layout: { background: { type: "solid", color: "transparent" }, textColor: "#94a3b8" },
        grid: { vertLines: { color: "rgba(255,255,255,0.05)" }, horzLines: { color: "rgba(255,255,255,0.05)" } },
        timeScale: { timeVisible: false, borderColor: "rgba(255,255,255,0.1)" },
        rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
    });

    const eqSeries = chart.addAreaSeries({
        lineColor: "#3b82f6",
        topColor: "rgba(59, 130, 246, 0.4)",
        bottomColor: "rgba(59, 130, 246, 0.0)",
        lineWidth: 2,
    });
    eqSeries.setData(curveData.map(d => ({ time: d.date, value: d.equity })));

    if (benchData) {
        const bSeries = chart.addLineSeries({ color: "#94a3b8", lineWidth: 1, lineStyle: 2 });
        bSeries.setData(benchData.map(d => ({ time: d.date, value: d.equity })));
    }

    // Average cost basis overlay (on its own scale) — only when we have any.
    const hasAvgCost = curveData.some(d => d.avg_cost != null);
    if (hasAvgCost) {
        const avgSeries = chart.addLineSeries({
            color: "#f59e0b",
            lineWidth: 1,
            lineStyle: 0,
            priceScaleId: "avg_cost",
            title: "Avg cost",
        });
        chart.priceScale("avg_cost").applyOptions({
            scaleMargins: { top: 0.1, bottom: 0.4 },
            borderColor: "rgba(245, 158, 11, 0.2)",
        });
        // Only feed points where we hold something; gaps render as breaks.
        const pts = curveData
            .filter(d => d.avg_cost != null)
            .map(d => ({ time: d.date, value: d.avg_cost }));
        avgSeries.setData(pts);
    }

    chart.timeScale().fitContent();
    window.addEventListener("resize", () => chart.applyOptions({ width: container.clientWidth }));
}

// ── Saved strategies ─────────────────────────────────────────────────────────

async function loadSavedStrategiesList() {
    try {
        const res = await fetch(`${apiBase}/backtest/strategies`);
        if (!res.ok) return;
        const data = await res.json();
        const select = document.getElementById("saved-strategies");
        select.innerHTML = '<option value="">Load Strategy...</option>';
        savedStrategiesIndex = {};
        savedStrategiesById = {};
        (data.strategies || []).forEach(s => {
            savedStrategiesIndex[s.id] = s.name;
            savedStrategiesById[s.id] = s;
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.textContent = s.name;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error("Failed to load strategies", e);
    }
}

function loadSelectedStrategy() {
    const sel = document.getElementById("saved-strategies");
    const id = sel.value;
    const deleteBtn = document.getElementById("delete-strategy-btn");
    if (!id) {
        deleteBtn.style.display = "none";
        return;
    }
    deleteBtn.style.display = "inline-block";
    const record = savedStrategiesById[id];
    if (!record || !record.definition) {
        alert("Strategy definition is missing — cannot load.");
        return;
    }
    applyStrategyToForm(record.definition);
}

async function deleteSelectedStrategy() {
    const sel = document.getElementById("saved-strategies");
    const id = sel.value;
    if (!id) return;
    if (!confirm(`Delete strategy "${savedStrategiesIndex[id]}"?`)) return;
    try {
        const res = await fetch(`${apiBase}/backtest/strategies/${id}`, { method: "DELETE" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadSavedStrategiesList();
        document.getElementById("delete-strategy-btn").style.display = "none";
    } catch (e) {
        alert("Failed to delete: " + e.message);
    }
}

function applyStrategyToForm(def) {
    // Sizing
    const sizing = def.position_sizing || {};
    if (sizing.method) document.getElementById("sizing-method").value = sizing.method;
    toggleSizingInputs();
    if (sizing.value != null) document.getElementById("sizing-value").value = sizing.value;
    if (sizing.risk_pct != null) document.getElementById("risk-pct").value = sizing.risk_pct;

    // Exits
    const ex = def.exit || {};
    document.getElementById("stop-loss").value = ex.stop_loss_pct ?? "";
    document.getElementById("take-profit").value = ex.take_profit_pct ?? "";
    document.getElementById("trailing-stop").value = ex.trailing_stop_pct ?? "";
    document.getElementById("max-hold").value = ex.max_hold_days ?? "";
    if (ex.pyramiding_exit_mode) document.getElementById("pyr-exit").value = ex.pyramiding_exit_mode;

    // Options
    const opt = def.options || {};
    document.getElementById("options-enabled").checked = !!opt.enabled;
    if (opt.type) document.getElementById("option-type").value = opt.type;
    if (opt.position) document.getElementById("option-position").value = opt.position;
    if (opt.target_dte != null) document.getElementById("option-dte").value = opt.target_dte;
    if (opt.target_delta != null) document.getElementById("option-delta").value = opt.target_delta;
    toggleOptionsInputs();

    // Pyramiding — supports both new and legacy fields
    const pyr = def.pyramiding || {};
    document.getElementById("pyr-enabled").checked = !!pyr.enabled;
    if (pyr.max_positions != null) document.getElementById("pyr-max").value = pyr.max_positions;
    let triggerMode = pyr.trigger_mode;
    if (!triggerMode && pyr.scale_in_trigger) {
        triggerMode = pyr.scale_in_trigger === "pullback" ? "pullback_only" : "signal_only";
    }
    if (triggerMode) document.getElementById("pyr-trigger-mode").value = triggerMode;
    const pullbackPct = pyr.pullback_pct ?? pyr.scale_in_value;
    if (pullbackPct != null) document.getElementById("pyr-pullback-pct").value = pullbackPct;
    if (pyr.pullback_reference) document.getElementById("pyr-pullback-ref").value = pyr.pullback_reference;
    togglePyrInputs();
    updatePyrFieldVisibility();

    // Rebuild conditions from the entry/exit trees
    rebuildConditionsFromTree("entry-conditions-container", "entry-operator-toggle", def.entry);
    rebuildConditionsFromTree("exit-conditions-container", "exit-operator-toggle", ex.conditions);
    updateEntryWarning();
}

function rebuildConditionsFromTree(containerId, toggleId, tree) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";
    if (!tree || !tree.conditions || tree.conditions.length === 0) {
        return;
    }
    // Set AND/OR toggle
    const op = (tree.operator || "AND").toUpperCase();
    document.querySelectorAll(`#${toggleId} .toggle-btn`).forEach(b => {
        b.classList.toggle("active", b.dataset.val === op);
    });
    tree.conditions.forEach(c => {
        addCondition(containerId);
        const lastRow = container.querySelector(".condition-node:last-child");
        applyConditionToRow(lastRow, c);
    });
}

function applyConditionToRow(row, cond) {
    const indSel = row.querySelector(".cond-indicator");
    if (cond.type === "pullback") {
        indSel.value = "PULLBACK";
        updateConditionParams(row.id);
        row.querySelector(".pb-pct").value = cond.pct;
        row.querySelector(".pb-lookback").value = cond.lookback;
        return;
    }
    if (cond.type === "consecutive") {
        indSel.value = "CONSECUTIVE";
        updateConditionParams(row.id);
        row.querySelector(".cons-count").value = cond.count;
        row.querySelector(".cons-dir").value = cond.direction;
        return;
    }

    const indName = (cond.indicator?.name || "RSI").toUpperCase();
    indSel.value = indName;
    updateConditionParams(row.id);

    const params = cond.indicator?.params || {};
    if (row.querySelector(".param-period") && params.period != null) row.querySelector(".param-period").value = params.period;
    if (row.querySelector(".param-stddev") && params.std_dev != null) row.querySelector(".param-stddev").value = params.std_dev;
    if (row.querySelector(".param-fast") && params.fast != null) row.querySelector(".param-fast").value = params.fast;
    if (row.querySelector(".param-slow") && params.slow != null) row.querySelector(".param-slow").value = params.slow;

    const typeSel = row.querySelector(".cond-type");
    typeSel.value = cond.type || "threshold";
    updateConditionType(row.id);

    if (cond.type === "threshold") {
        const cmp = row.querySelector(".cond-comparator");
        if (cmp) cmp.value = cond.comparator || "lt";
        const tgtVal = row.querySelector(".target-value");
        if (tgtVal) tgtVal.value = cond.value ?? 0;
    } else if (cond.type === "reference") {
        const cmp = row.querySelector(".cond-comparator");
        if (cmp) cmp.value = cond.comparator || "gt";
        const refName = row.querySelector(".target-indicator");
        if (refName && cond.ref_indicator?.name) refName.value = cond.ref_indicator.name;
        const refPeriod = row.querySelector(".target-period");
        if (refPeriod && cond.ref_indicator?.params?.period != null) refPeriod.value = cond.ref_indicator.params.period;
    } else if (cond.type === "crossover") {
        const dirSel = row.querySelector(".cond-direction");
        if (dirSel) dirSel.value = cond.direction || "above";
        const refName = row.querySelector(".target-indicator");
        if (refName && cond.ref_indicator?.name) refName.value = cond.ref_indicator.name;
        const refPeriod = row.querySelector(".target-period");
        if (refPeriod && cond.ref_indicator?.params?.period != null) refPeriod.value = cond.ref_indicator.params.period;
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
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadSavedStrategiesList();
        alert("Strategy saved.");
    } catch (e) {
        alert("Failed to save strategy: " + e.message);
    }
}
