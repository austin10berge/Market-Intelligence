const API_BASE = "http://127.0.0.1:8000/api";

document.addEventListener("DOMContentLoaded", () => {
    initDashboard();
});

async function initDashboard() {
    // Fire off all requests concurrently
    Promise.allSettled([
        fetchMarketPosture(),
        fetchCspCandidates(),
        fetchLeapsCandidates()
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
        renderCspCandidates(data.candidates);
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
