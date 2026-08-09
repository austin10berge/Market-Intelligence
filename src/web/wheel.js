const API = window.MARKET_INTELLIGENCE_CONFIG?.apiBase ?? '';

function fmt(val, prefix='$', decimals=2) {
    if (val == null) return '—';
    const n = parseFloat(val);
    return (n < 0 ? '-' + prefix : prefix) + Math.abs(n).toFixed(decimals);
}

async function loadStats() {
    try {
        const res = await fetch(API + '/wheel/stats');
        const d = await res.json();
        document.getElementById('stat-mtd').textContent = fmt(d.premium_mtd);
        document.getElementById('stat-ytd').textContent = fmt(d.premium_ytd);
        document.getElementById('stat-winrate').textContent =
            d.win_rate != null ? (d.win_rate * 100).toFixed(1) + '%' : '—';
        document.getElementById('stat-open').textContent =
            d.open_cycles != null ? d.open_cycles : '—';
        document.getElementById('stat-delta').textContent =
            d.max_short_put_delta != null ? d.max_short_put_delta.toFixed(2) : '—';
    } catch (e) {
        console.error('stats load error', e);
        ['stat-mtd', 'stat-ytd', 'stat-winrate', 'stat-open', 'stat-delta'].forEach(id => {
            document.getElementById(id).textContent = 'Error';
        });
    }
}

async function loadPositions() {
    try {
        const res = await fetch(API + '/wheel/positions');
        const { positions } = await res.json();
        const container = document.getElementById('positions-rows');
        const headers = document.getElementById('positions-headers');

        if (!positions.length) {
            container.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">No open positions.</div>';
            return;
        }
        headers.style.display = '';
        container.innerHTML = positions.map(p => {
            const pnlColor = (p.unrealized_pnl ?? 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            const deltaStr = p.delta != null ? p.delta.toFixed(2) : '—';
            return `<div class="table-row" style="display:grid;grid-template-columns:repeat(9,1fr);padding:.5rem 1rem;border-bottom:1px solid var(--border);">
                <div>${p.symbol}</div>
                <div>${p.option_type ?? p.asset_type}</div>
                <div>${p.strike != null ? '$' + p.strike : '—'}</div>
                <div>${p.expiration ?? '—'}</div>
                <div>${p.dte ?? '—'}</div>
                <div>${p.quantity}</div>
                <div>${fmt(p.average_price)}</div>
                <div style="color:${pnlColor}">${fmt(p.unrealized_pnl)}</div>
                <div>${deltaStr}</div>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('positions load error', e);
        document.getElementById('positions-rows').innerHTML =
            '<div class="trade-item">Error fetching positions</div>';
    }
}

async function loadCycles() {
    try {
        const res = await fetch(API + '/wheel/cycles?limit=100');
        const { cycles } = await res.json();
        const container = document.getElementById('cycles-list');

        if (!cycles.length) {
            container.innerHTML = '<div style="padding:1rem;color:var(--text-secondary)">No wheel cycles yet. Run a pipeline sync after trading.</div>';
            return;
        }

        container.innerHTML = cycles.map(c => {
            const statusColor = c.status === 'OPEN' ? 'var(--accent-blue)' : 'var(--text-secondary)';
            const pnlStr = c.realized_pnl != null ? fmt(c.realized_pnl) : (c.status === 'OPEN' ? 'open' : '—');
            const tradesHtml = (c.trades || []).map(t =>
                `<div style="padding:.25rem 1rem;font-size:.8rem;color:var(--text-secondary);">
                    ${t.executed_at?.slice(0,10)} &nbsp; ${t.instruction} &nbsp; ${t.symbol} &nbsp; ${fmt(t.net_amount)}
                </div>`
            ).join('');
            return `<details style="border-bottom:1px solid var(--border);padding:.75rem 1rem;">
                <summary style="cursor:pointer;display:flex;gap:2rem;align-items:center;list-style:none;">
                    <span style="font-weight:600;">${c.underlying}</span>
                    <span style="color:${statusColor};font-size:.82rem;">${c.status}</span>
                    <span>${c.opened_at ?? '—'} → ${c.closed_at ?? '…'}</span>
                    <span>Premium: ${fmt(c.total_premium)}</span>
                    <span>P/L: ${pnlStr}</span>
                </summary>
                ${tradesHtml || '<div style="padding:.5rem 1rem;font-size:.8rem;color:var(--text-secondary);">No trade legs linked.</div>'}
            </details>`;
        }).join('');
    } catch (e) {
        console.error('cycles load error', e);
        document.getElementById('cycles-list').innerHTML =
            '<div class="trade-item">Error fetching wheel cycles</div>';
    }
}

loadStats();
loadPositions();
loadCycles();
