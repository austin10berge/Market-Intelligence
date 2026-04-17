const API_BASE = "http://127.0.0.1:8000/api";

// CSP State
let allCspCandidates = [];
let currentCspSort = 'roc_percent';
let currentCspSortDesc = true;
let currentCspPage = 1;
const CSP_PER_PAGE = 5;

// Stock State
let allStockCandidates = [];
let currentStockSort = { column: 'pct_1d', asc: false };

document.addEventListener("DOMContentLoaded", () => {
    initDashboard();
});

async function initDashboard() {
    // Fire off all requests concurrently
    Promise.allSettled([
        fetchMarketPosture(),
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
        renderSignals(data);
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
        renderLeapsCandidates(data.candidates);
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
        sortStocks(currentStockSort.column, currentStockSort.asc);
    } catch (e) {
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

function renderSignals(data) {
    const signalsList = document.getElementById("signals-list");
    signalsList.classList.remove("loading");
    signalsList.innerHTML = "";

    if (data.signals && data.signals.length > 0) {
        data.signals.forEach(s => {
            let className = "neutral";
            if (s.scored_value > 0) className = "bullish";
            if (s.scored_value < 0) className = "bearish";

            const sourceName = s.source.replace("_", " ").toUpperCase();

            signalsList.innerHTML += `
                <div class="signal-item ${className}">
                    <div class="signal-title">${sourceName}</div>
                    <div class="signal-val">${s.summary}</div>
                </div>
            `;
        });
    }

    // Render LLM summary
    const llmBox = document.getElementById("llm-summary");
    if (data.llm_summary) {
        llmBox.innerText = data.llm_summary;
    } else {
        llmBox.innerText = "No AI analysis available for today.";
    }
}

function renderCspCandidates(candidates) {
    const list = document.getElementById("csp-list");
    list.classList.remove("loading");

    if (!candidates || candidates.length === 0) {
        list.innerHTML = "<div class='trade-item'>No viable candidates found.</div>";
        return;
    }

    list.innerHTML = "";
    candidates.forEach(c => {
        list.innerHTML += `
            <div class="trade-item">
                <div class="ticker-block">
                    <span class="ticker">${c.symbol}</span>
                    <span class="ticker-sub">Stock: $${c.current_price.toFixed(2)}</span>
                </div>
                <div class="metric">
                    <span class="m-val">$${c.strike.toFixed(2)}</span>
                    <span class="m-lbl">Strike</span>
                </div>
                <div class="metric">
                    <span class="m-val">$${c.premium.toFixed(2)}</span>
                    <span class="m-lbl">Premium</span>
                </div>
                <div class="metric">
                    <span class="m-val highlight">${c.roc_percent.toFixed(1)}%</span>
                    <span class="m-lbl">Capital ROC</span>
                </div>
                <div class="metric">
                    <span class="m-val highlight">${c.otm_percent.toFixed(1)}%</span>
                    <span class="m-lbl">% OTM</span>
                </div>
            </div>
        `;
    });
}

function renderLeapsCandidates(candidates) {
    const list = document.getElementById("leaps-list");
    list.classList.remove("loading");

    if (!candidates || candidates.length === 0) {
        list.innerHTML = "<div class='trade-item'>No viable candidates found.</div>";
        return;
    }

    list.innerHTML = "";
    candidates.forEach(c => {
        list.innerHTML += `
            <div class="trade-item">
                <div class="ticker-block">
                    <span class="ticker">${c.symbol}</span>
                    <span class="ticker-sub">Stock: $${c.current_price.toFixed(2)}</span>
                </div>
                <div class="metric">
                    <span class="m-val">$${c.strike.toFixed(2)}</span>
                    <span class="m-lbl">Strike</span>
                </div>
                <div class="metric">
                    <span class="m-val">$${c.premium.toFixed(2)}</span>
                    <span class="m-lbl">Premium</span>
                </div>
                <div class="metric">
                    <span class="m-val highlight">${c.premium_markup_percent.toFixed(1)}%</span>
                    <span class="m-lbl">Markup</span>
                </div>
            </div>
        `;
    });
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

        return `
            <div class="trade-item">
                <div class="ticker-block">
                    <span class="symbol">${c.symbol}</span>
                </div>
                <div class="metric" style="color: var(--text-secondary);">${c.name}</div>
                <div class="metric"><span class="m-val">$${c.price.toFixed(2)}</span></div>
                <div class="metric" style="color: var(--text-secondary); font-size: 0.8rem;">${c.sector}</div>
                <div class="metric ${d1Class}">${d1Sign}${c.pct_1d.toFixed(2)}%</div>
                <div class="metric ${w1Class}">${w1Sign}${c.pct_1w.toFixed(2)}%</div>
                <div class="metric ${m1Class}">${m1Sign}${c.pct_1m.toFixed(2)}%</div>
                <div class="metric">${c.pe}</div>
                <div class="metric">${c.beta}</div>
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
