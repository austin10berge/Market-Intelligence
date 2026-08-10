window.WheelView = (() => {

    function esc(s) {
        return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    function fmtMoney(v, decimals = 0) {
        if (v == null) return '—';
        const n = parseFloat(v);
        if (isNaN(n)) return '—';
        return `${n >= 0 ? '' : '-'}$${Math.abs(n).toFixed(decimals)}`;
    }

    function fmtDelta(v) {
        if (v == null) return '—';
        return parseFloat(v).toFixed(2);
    }

    function fmtDate(s) {
        if (!s) return '—';
        return s.slice(0, 10);
    }

    function dteColor(dte) {
        if (dte == null) return 'var(--tv-muted)';
        return dte <= 7 ? 'var(--tv-red)' : dte <= 14 ? 'var(--tv-yellow)' : 'var(--tv-muted)';
    }

    function renderStats(s) {
        const winPct  = s.win_rate != null ? `${(s.win_rate * 100).toFixed(0)}%` : '—';
        const maxDelta = s.max_short_put_delta != null ? parseFloat(s.max_short_put_delta).toFixed(2) : '—';
        const deltaAlert = s.max_short_put_delta != null && parseFloat(s.max_short_put_delta) >= 0.30;
        return `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:10px 14px 4px">
            <div class="overview-card">
                <div class="overview-card-title">Premium MTD</div>
                <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:var(--tv-green)">${fmtMoney(s.premium_mtd)}</div>
            </div>
            <div class="overview-card">
                <div class="overview-card-title">Premium YTD</div>
                <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:var(--tv-green)">${fmtMoney(s.premium_ytd)}</div>
            </div>
            <div class="overview-card">
                <div class="overview-card-title">Win Rate</div>
                <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600">${winPct}</div>
                <div style="font-size:12px;color:var(--tv-muted);margin-top:2px">${s.total_cycles ?? 0} cycles · ${s.open_cycles ?? 0} open</div>
            </div>
            <div class="overview-card">
                <div class="overview-card-title">Max Short Δ</div>
                <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:${deltaAlert?'var(--tv-red)':'var(--tv-text)'}">${maxDelta}</div>
            </div>
        </div>`;
    }

    function renderPositions(positions) {
        const opts = positions.filter(p => p.asset_type === 'OPTION');
        if (!opts.length) return `<div class="list-message">No open option positions</div>`;
        return opts.map((p, i) => {
            const isShort  = (p.quantity ?? 0) < 0;
            const dte      = p.dte;
            const pnl      = p.unrealized_pnl;
            const pnlCls   = pnl > 0 ? 'var(--tv-green)' : pnl < 0 ? 'var(--tv-red)' : 'var(--tv-muted)';
            const dAlert   = p.delta != null && Math.abs(parseFloat(p.delta)) >= 0.30;
            return `
            <div class="option-card${isShort?' up':''}" style="--row-delay:${i*30}ms">
                <div class="oc-row1">
                    <div class="oc-sym-wrap">
                        <span class="oc-symbol">${esc(p.underlying || p.symbol)}</span>
                        <span class="oc-subname">${esc(p.option_type||'')} ${p.strike?'$'+parseFloat(p.strike).toFixed(0):''} · ${fmtDate(p.expiration)}</span>
                    </div>
                    <span class="oc-highlight">${isShort?'SHORT':'LONG'}</span>
                </div>
                <div class="oc-row2">
                    <span class="oc-name">DTE <span style="color:${dteColor(dte)};font-weight:600">${dte!=null?dte:'—'}</span></span>
                    <span class="oc-metrics">
                        Δ <span style="color:${dAlert?'var(--tv-red)':'inherit'}">${fmtDelta(p.delta)}</span>
                        &nbsp;·&nbsp;
                        <span style="color:${pnlCls}">${pnl!=null?(pnl>=0?'+':'')+fmtMoney(pnl):'—'}</span>
                    </span>
                </div>
            </div>`;
        }).join('');
    }

    function renderCycles(cycles) {
        if (!cycles.length) return `<div class="list-message">No wheel cycles yet — run the nightly sync to populate</div>`;
        return cycles.map((cyc, i) => {
            const isOpen   = cyc.status !== 'CLOSED';
            const pnl      = cyc.realized_pnl;
            const pnlColor = pnl != null ? (pnl >= 0 ? 'var(--tv-green)' : 'var(--tv-red)') : 'var(--tv-muted)';
            const statusBg = isOpen ? 'rgba(41,98,255,0.12)' : 'rgba(120,123,134,0.12)';
            const statusClr= isOpen ? '#5B8AF5' : 'var(--tv-muted)';
            const trades   = cyc.trades || [];
            const tradeRows = trades.map(t => `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--tv-border)">
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tv-muted)">
                        ${esc(t.option_type||'')} $${parseFloat(t.strike||0).toFixed(0)} · ${(t.executed_at||'').slice(0,10)}
                    </span>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;font-weight:600;color:${(t.net_amount||0)>=0?'var(--tv-green)':'var(--tv-red)'}">
                        ${t.net_amount!=null?((t.net_amount>=0?'+':'')+fmtMoney(t.net_amount)):'—'}
                    </span>
                </div>`).join('');
            return `
            <div class="overview-card" style="margin:6px 14px;animation:row-in 0.32s ease both;animation-delay:${i*40}ms">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:${trades.length?'8px':'0'}">
                    <div style="display:flex;align-items:center;gap:8px">
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:#fff">${esc(cyc.underlying||'')}</span>
                        <span style="font-size:11px;padding:2px 6px;border-radius:4px;background:${statusBg};color:${statusClr};font-family:'IBM Plex Mono',monospace;font-weight:600;letter-spacing:0.03em">${esc(cyc.status||'')}</span>
                    </div>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;color:${pnlColor}">
                        ${pnl!=null?((pnl>=0?'+':'')+fmtMoney(pnl)):'open'}
                    </span>
                </div>
                ${trades.length ? `
                <details>
                    <summary style="cursor:pointer;font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted);letter-spacing:0.05em;text-transform:uppercase;list-style:none;padding:2px 0">
                        ${trades.length} leg${trades.length>1?'s':''}
                    </summary>
                    <div style="margin-top:4px">${tradeRows}</div>
                </details>` : ''}
            </div>`;
        }).join('');
    }

    function render(el) {
        el.innerHTML = `
            <div class="scanner-header">
                <span class="scanner-title">Wheel Tracker</span>
                <span class="data-freshness-badge" id="whl-badge"></span>
            </div>
            <div id="whl-stats"><div class="list-message loading">Loading…</div></div>
            <div class="section-header" style="padding-top:8px">
                <span class="section-title">Open Positions</span>
            </div>
            <div id="whl-positions"><div class="list-message loading">Loading…</div></div>
            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Wheel Cycles</span>
            </div>
            <div id="whl-cycles" style="padding-bottom:16px"><div class="list-message loading">Loading…</div></div>
        `;

        const base = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || '';
        Promise.all([
            fetch(`${base}/wheel/stats`).then(r => r.json()),
            fetch(`${base}/wheel/positions`).then(r => r.json()),
            fetch(`${base}/wheel/cycles`).then(r => r.json()),
        ]).then(([stats, posData, cycData]) => {
            if (!document.getElementById('whl-stats')) return;
            document.getElementById('whl-stats').innerHTML     = renderStats(stats);
            document.getElementById('whl-positions').innerHTML = renderPositions(posData.positions || []);
            document.getElementById('whl-cycles').innerHTML    = renderCycles(cycData.cycles || []);
            const badge = document.getElementById('whl-badge');
            if (badge) { badge.className = 'data-freshness-badge fresh'; badge.textContent = 'Live'; }
        }).catch(err => {
            console.error('[WheelView]', err);
            const el = document.getElementById('whl-stats');
            if (el) el.innerHTML = `<div class="list-message">Failed to load — check API connection</div>`;
        });
    }

    function teardown() {}

    return { render, teardown };
})();
