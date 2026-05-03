import {
    DEFAULT_INDICATOR_SETTINGS,
    INDICATOR_STORAGE_KEY,
    sanitizeIndicatorSettings,
    buildWidgetStudies,
    buildWidgetStudyOverrides,
} from "./technical-analysis-helpers.js";

const API_BASE = window.MARKET_INTELLIGENCE_CONFIG?.apiBase || "/MISSING_CONFIG_JS_SEE_CONSOLE";

let candidates = [];
/** Map of symbol → stock screener row, used to fill in IV/RV20, IV PCT, P/E */
let stockDataBySymbol = {};
let indicatorSettings = loadIndicatorSettings();
let widgets = [];

document.addEventListener("DOMContentLoaded", async () => {
    renderControls();
    // Fetch CSP candidates and stock screener data concurrently so the stock
    // screener's already-computed IV/RV20, IV PCT, and P/E values are available
    // when we render each chart card.
    await Promise.allSettled([loadCandidates(), loadStockData()]);
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
        return raw ? sanitizeIndicatorSettings(JSON.parse(raw)) : DEFAULT_INDICATOR_SETTINGS;
    } catch {
        return DEFAULT_INDICATOR_SETTINGS;
    }
}

function persistIndicatorSettings() {
    try {
        window.localStorage.setItem(INDICATOR_STORAGE_KEY, JSON.stringify(indicatorSettings));
    } catch {
        // Preserve interactivity even if storage is unavailable.
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
        if (!response.ok) {
            throw new Error("Failed to fetch CSP candidates");
        }

        const payload = await response.json();
        candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
        await renderCandidates();
    } catch (error) {
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
    await renderCandidates();
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

async function renderCandidates() {
    destroyWidgets();

    const tickerCandidates = dedupeCandidatesBySymbol(candidates);

    if (!tickerCandidates.length) {
        setMessage("No viable CSP candidates found.");
        getListEl().innerHTML = "";
        return;
    }

    setMessage(`${tickerCandidates.length} tickers loaded from ${candidates.length} CSP candidates.`);
    getListEl().innerHTML = tickerCandidates.map((candidate, index) => {
        const stockMetrics = resolveStockMetrics(candidate);
        return `
        <section class="glass card full-width ta-ticker-card" data-symbol="${escapeHtml(candidate.symbol || "")}">
            <div class="card-header">
                <div>
                    <h2>${escapeHtml(candidate.symbol || "Unknown")}</h2>
                    <p class="subtitle">${buildContractSummary(candidate)}</p>
                </div>
            </div>
            <div class="ta-stock-grid">
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
        renderTradingViewWidget(candidate, index);
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

function renderTradingViewWidget(candidate, index) {
    const containerId = `ta-chart-${index}`;
    const container = document.getElementById(containerId);
    if (!container) return;

    const studies = buildWidgetStudies(indicatorSettings);
    const config = {
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
    };

    const widgetDiv = document.createElement("div");
    widgetDiv.className = "tradingview-widget-container__widget";
    widgetDiv.style.cssText = "height:100%;width:100%";
    container.appendChild(widgetDiv);

    const script = document.createElement("script");
    script.type = "text/javascript";
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.textContent = JSON.stringify(config);
    container.appendChild(script);
}

function destroyWidgets() {
    document.querySelectorAll(".ta-chart-shell").forEach((el) => {
        el.innerHTML = "";
    });
    widgets = [];
}

function resolveTradingViewSymbol(symbol) {
    return symbol || "SPY";
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
