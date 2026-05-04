import {
    DEFAULT_INDICATOR_SETTINGS,
    INDICATOR_STORAGE_KEY,
    sanitizeIndicatorSettings,
    buildWidgetStudies,
    loadScannerParams,
    buildScanQueryString,
} from "./technical-analysis-helpers.js";

const API_BASE = window.MARKET_INTELLIGENCE_CONFIG?.apiBase || "/MISSING_CONFIG_JS_SEE_CONSOLE";

let candidates = [];
let stockDataBySymbol = {};
let widgets = [];
let activeTab = "watchlist";   // "watchlist" | "universe"
let universeData = null;       // null = not yet loaded; [] = loaded (may be empty)
let indicatorSettings = loadIndicatorSettings(); // must come after activeTab declaration

document.addEventListener("DOMContentLoaded", async () => {
    renderControls();
    initTabs();

    const refreshBtn = document.getElementById("btn-universe-refresh");
    refreshBtn.style.display = activeTab === "universe" ? "" : "none";

    if (activeTab === "universe") {
        await Promise.allSettled([loadUniverseCandidates(), loadStockData()]);
    } else {
        await Promise.allSettled([loadCandidates(), loadStockData()]);
    }
});

function getControlsEl() {
    return document.getElementById("indicator-controls");
}

function getListEl() {
    return document.getElementById("technical-analysis-list");
}

function getMessageEl() {
    return document.getElementById("technical-analysis-message");
}

function loadIndicatorSettings() {
    try {
        const raw = window.localStorage.getItem(INDICATOR_STORAGE_KEY);
        if (!raw) return DEFAULT_INDICATOR_SETTINGS;
        const parsed = JSON.parse(raw);
        if (parsed.activeTab === "universe") activeTab = "universe";
        return sanitizeIndicatorSettings(parsed);
    } catch {
        return DEFAULT_INDICATOR_SETTINGS;
    }
}

function persistIndicatorSettings() {
    try {
        window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify({
            ...indicatorSettings,
            activeTab,
        }));
    } catch {
        // Preserve interactivity even if storage is unavailable.
    }
}

function initTabs() {
    document.getElementById("tab-watchlist").classList.toggle("active", activeTab === "watchlist");
    document.getElementById("tab-universe").classList.toggle("active", activeTab === "universe");
}

window.switchTab = async function switchTab(tab) {
    if (tab === activeTab) return;
    activeTab = tab;
    persistIndicatorSettings();

    document.getElementById("tab-watchlist").classList.toggle("active", tab === "watchlist");
    document.getElementById("tab-universe").classList.toggle("active", tab === "universe");

    const refreshBtn = document.getElementById("btn-universe-refresh");
    refreshBtn.style.display = tab === "universe" ? "" : "none";

    if (tab === "watchlist") {
        await renderCandidates(candidates);
    } else {
        if (universeData !== null) {
            await renderCandidates(universeData);
        } else {
            await loadUniverseCandidates();
        }
    }
};

window.refreshUniverse = async function refreshUniverse() {
    universeData = null;
    await loadUniverseCandidates();
};

async function loadUniverseCandidates() {
    setMessage("Loading universe candidates…");
    destroyWidgets();
    getListEl().innerHTML = "";

    try {
        const params = loadScannerParams();
        const qs = buildScanQueryString(params);
        const response = await fetch(`${API_BASE}/screener/csp-scan?${qs}`);
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const payload = await response.json();
        universeData = Array.isArray(payload.candidates) ? payload.candidates : [];
        if (activeTab === "universe") {
            await renderCandidates(universeData);
        }
    } catch (error) {
        if (activeTab !== "universe") return;
        setMessage("Unable to load universe candidates.");
        getListEl().innerHTML = `
            <section class="glass card full-width">
                <p class="subtitle">${escapeHtml(error.message)}</p>
            </section>
        `;
    }
}

async function loadStockData() {
    try {
        const response = await fetch(`${API_BASE}/screener/stocks`);
        if (!response.ok) return;
        const payload = await response.json();
        const rows = Array.isArray(payload.candidates) ? payload.candidates : [];
        stockDataBySymbol = Object.fromEntries(rows.map((r) => [r.symbol, r]));
    } catch {
        // Non-fatal — metrics will fall back to whatever the CSP response provides.
    }
}

async function loadCandidates() {
    setMessage("Loading CSP candidates...");

    try {
        const response = await fetch(`${API_BASE}/screener/csp`);
        if (!response.ok) throw new Error("Failed to fetch CSP candidates");
        const payload = await response.json();
        candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
        if (activeTab === "watchlist") {
            await renderCandidates(candidates);
        }
    } catch (error) {
        if (activeTab !== "watchlist") return;
        setMessage("Unable to load CSP candidates.");
        getListEl().innerHTML = `
            <section class="glass card full-width">
                <p class="subtitle">${escapeHtml(error.message)}</p>
            </section>
        `;
    }
}

function renderControls() {
    const controlsEl = getControlsEl();

    controlsEl.innerHTML = `
        ${indicatorSettings.sma.map((item, index) => `
            <div class="ta-control-group">
                <span>SMA ${index + 1}</span>
                <div class="ta-control-inline">
                    <input
                        type="checkbox"
                        id="sma-enabled-${index}"
                        data-kind="sma-enabled"
                        data-index="${index}"
                        ${item.enabled ? "checked" : ""}
                    >
                    <label for="sma-enabled-${index}">Enabled</label>
                </div>
                <input
                    type="number"
                    min="2"
                    max="400"
                    data-kind="sma-length"
                    data-index="${index}"
                    value="${item.length}"
                >
            </div>
        `).join("")}
        <div class="ta-control-group">
            <span>Bollinger Bands</span>
            <div class="ta-control-inline">
                <input
                    type="checkbox"
                    id="bb-enabled"
                    data-kind="bb-enabled"
                    ${indicatorSettings.bollinger.enabled ? "checked" : ""}
                >
                <label for="bb-enabled">Enabled</label>
            </div>
            <input
                type="number"
                min="2"
                max="400"
                data-kind="bb-length"
                value="${indicatorSettings.bollinger.length}"
            >
            <input
                type="number"
                min="0.1"
                max="5"
                step="0.1"
                data-kind="bb-multiplier"
                value="${indicatorSettings.bollinger.multiplier}"
            >
        </div>
    `;

    controlsEl.onchange = handleControlsChanged;
}

async function handleControlsChanged() {
    const controlsEl = getControlsEl();
    const next = {
        sma: indicatorSettings.sma.map((item, index) => ({
            enabled: controlsEl.querySelector(`[data-kind="sma-enabled"][data-index="${index}"]`).checked,
            length: controlsEl.querySelector(`[data-kind="sma-length"][data-index="${index}"]`).value,
        })),
        bollinger: {
            enabled: controlsEl.querySelector("[data-kind='bb-enabled']").checked,
            length: controlsEl.querySelector("[data-kind='bb-length']").value,
            multiplier: controlsEl.querySelector("[data-kind='bb-multiplier']").value,
        },
        interval: indicatorSettings.interval,
        theme: indicatorSettings.theme,
    };

    indicatorSettings = sanitizeIndicatorSettings(next);
    persistIndicatorSettings();
    renderControls();
    const source = activeTab === "universe" ? (universeData ?? []) : candidates;
    await renderCandidates(source);
}

/**
 * Merge the best available values for the three metrics that are reliably
 * provided by the stock screener but may be stale / missing on CSP candidates.
 */
function resolveStockMetrics(candidate) {
    const stock = stockDataBySymbol[candidate.symbol];
    // Stock screener returns "N/A" (string) when a value couldn't be computed.
    // Normalise those to null so the format helpers render "—" correctly.
    const clean = (v) => (v === "N/A" || v === undefined ? null : v);
    return {
        atm_iv_rv20: clean(stock?.atm_iv_rv20) ?? clean(candidate.atm_iv_rv20),
        iv_percentile: clean(stock?.iv_percentile) ?? clean(candidate.iv_percentile),
        pe: clean(stock?.pe) ?? clean(candidate.pe),
    };
}

async function renderCandidates(source) {
    destroyWidgets();

    const tickerCandidates = dedupeCandidatesBySymbol(source);

    if (!tickerCandidates.length) {
        const label = activeTab === "universe" ? "universe" : "CSP";
        setMessage(`No viable ${label} candidates found.`);
        getListEl().innerHTML = "";
        return;
    }

    const label = activeTab === "universe" ? "universe" : "CSP";
    setMessage(`${tickerCandidates.length} tickers loaded from ${source.length} ${label} candidates.`);
    getListEl().innerHTML = tickerCandidates.map((candidate, index) => {
        const stockMetrics = resolveStockMetrics(candidate);
        const score = Number.isFinite(candidate.composite_score)
            ? candidate.composite_score.toFixed(1)
            : "—";
        return `
        <section class="glass card full-width ta-ticker-card" data-symbol="${escapeHtml(candidate.symbol || "")}">
            <div class="card-header">
                <div>
                    <h2>${escapeHtml(candidate.symbol || "Unknown")}</h2>
                    <p class="subtitle">${buildContractSummary(candidate)}</p>
                </div>
            </div>
            <div class="ta-stock-grid">
                ${metricCell("Score", score)}
                ${metricCell("RSI", formatMetric(candidate.rsi))}
                ${metricCell("ADX", formatMetric(candidate.adx))}
                ${metricCell("IV/RV20", formatMetric(stockMetrics.atm_iv_rv20))}
                ${metricCell("IV PCT", formatPercent(stockMetrics.iv_percentile))}
                ${metricCell("P/E", formatMetric(stockMetrics.pe))}
                ${metricCell("P/FCF", formatMetric(candidate.p_free_cash_flow))}
            </div>
            <div class="ta-summary-grid">
                ${metricCell("Stock", formatMoney(candidate.current_price))}
                ${metricCell("Premium", formatMoney(candidate.premium))}
                ${metricCell("ROC", formatPercent(candidate.roc_percent))}
                ${metricCell("Ann. Yield", formatPercent(candidate.annualized_roc))}
                ${metricCell("% OTM", formatPercent(candidate.otm_percent))}
                ${metricCell("Spread", formatPercent(candidate.spread_pct))}
                ${metricCell("IV", formatPercent(candidate.impliedVolatility))}
                ${metricCell("Volume", formatInteger(candidate.volume))}
                ${metricCell("DTE", formatPlain(candidate.dte))}
            </div>
            <div id="ta-chart-${index}" class="ta-chart-shell"></div>
        </section>
    `;
    }).join("");

    for (const [index, candidate] of tickerCandidates.entries()) {
        await renderTradingViewWidget(candidate, index);
    }
}

function dedupeCandidatesBySymbol(allCandidates) {
    const seen = new Set();

    return allCandidates.filter((candidate) => {
        const symbol = candidate?.symbol;
        if (!symbol || seen.has(symbol)) {
            return false;
        }

        seen.add(symbol);
        return true;
    });
}

let tvLibReady = null;

function loadTvLib() {
    if (tvLibReady) return tvLibReady;
    tvLibReady = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://s3.tradingview.com/tv.js";
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    });
    return tvLibReady;
}

async function renderTradingViewWidget(candidate, index) {
    const containerId = `ta-chart-${index}`;
    const container = document.getElementById(containerId);
    if (!container) return;

    await loadTvLib();

    const studies = buildWidgetStudies(indicatorSettings);
    new window.TradingView.widget({
        container_id: containerId,
        autosize: true,
        symbol: candidate.symbol || "SPY",
        interval: indicatorSettings.interval,
        timezone: "exchange",
        theme: indicatorSettings.theme,
        style: "1",
        locale: "en",
        backgroundColor: "rgba(11, 15, 25, 1)",
        withdateranges: true,
        hide_side_toolbar: false,
        allow_symbol_change: false,
        save_image: false,
        studies,
    });
}

function destroyWidgets() {
    document.querySelectorAll(".ta-chart-shell").forEach((el) => {
        el.innerHTML = "";
    });
    widgets = [];
}


function buildContractSummary(candidate) {
    const expiration = candidate.expiration || "No expiration";
    const strike = Number.isFinite(candidate.strike) ? `${candidate.strike.toFixed(2)} put` : "Unknown strike";
    const dte = Number.isFinite(candidate.dte) ? `${candidate.dte} DTE` : "DTE unavailable";

    return escapeHtml(`${expiration} ${strike} · ${dte}`);
}

function metricCell(label, value) {
    return `
        <div class="metric">
            <span class="m-val">${escapeHtml(value)}</span>
            <span class="m-lbl">${escapeHtml(label)}</span>
        </div>
    `;
}

function formatMoney(value) {
    return Number.isFinite(value) ? value.toFixed(2) : "—";
}

function formatPercent(value) {
    return Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}

function formatInteger(value) {
    return Number.isFinite(value) && value > 0 ? value.toLocaleString() : "—";
}

function formatPlain(value) {
    return Number.isFinite(value) ? String(value) : "—";
}

function formatMetric(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
        return value.toFixed(2);
    }

    return value && value !== "N/A" ? String(value) : "—";
}

function setMessage(message) {
    getMessageEl().textContent = message;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}
