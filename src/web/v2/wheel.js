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

    function signedMoney(v) {
        if (v == null) return '—';
        const n = parseFloat(v);
        if (isNaN(n)) return '—';
        return `${n >= 0 ? '+' : '-'}$${Math.abs(n).toFixed(0)}`;
    }

    function moneyColor(v) {
        if (v == null) return 'var(--tv-muted)';
        return v >= 0 ? 'var(--tv-green)' : 'var(--tv-red)';
    }

    function dteColor(dte) {
        if (dte == null) return 'var(--tv-muted)';
        return dte <= 7 ? 'var(--tv-red)' : dte <= 14 ? 'var(--tv-yellow)' : 'var(--tv-muted)';
    }

    // ── Stats cards ──

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
                <div style="font-size:12px;color:var(--tv-muted);margin-top:2px">${s.total_tickers ?? 0} tickers · ${s.active_tickers ?? 0} active</div>
            </div>
            <div class="overview-card">
                <div class="overview-card-title">Max Short Δ</div>
                <div style="font-family:'IBM Plex Mono',monospace;font-size:20px;font-weight:600;color:${deltaAlert?'var(--tv-red)':'var(--tv-text)'}">${maxDelta}</div>
            </div>
        </div>`;
    }

    // ── Open Holdings (equity positions) ──

    function renderHoldings(positions) {
        const equities = positions.filter(p => p.asset_type === 'EQUITY');
        if (!equities.length) return `<div class="list-message">No equity holdings</div>`;
        return equities.map((p, i) => {
            const qty = Math.abs(p.quantity || 0);
            const avgCost = p.average_price || 0;
            const curPrice = p.current_price || 0;
            const costBasis = qty * avgCost;
            const curValue = p.market_value || (qty * curPrice);
            const unrealized = p.unrealized_pnl ?? (curValue - costBasis);
            const pnlColor = moneyColor(unrealized);
            return `
            <div class="option-card${unrealized >= 0 ? ' up' : ' down'}" style="--row-delay:${i*30}ms">
                <div class="oc-row1">
                    <div class="oc-sym-wrap">
                        <span class="oc-symbol">${esc(p.symbol)}</span>
                        <span class="oc-subname">${qty} shares · avg $${avgCost.toFixed(2)}</span>
                    </div>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:15px;font-weight:600;color:${pnlColor}">${fmtMoney(curValue)}</span>
                </div>
                <div class="oc-row2">
                    <span class="oc-name">Cost $${costBasis.toFixed(0)}</span>
                    <span class="oc-metrics" style="color:${pnlColor};font-weight:600">
                        ${signedMoney(unrealized)}
                    </span>
                </div>
            </div>`;
        }).join('');
    }

    // ── Open Trades (option positions) ──

    function renderOpenTrades(positions) {
        const opts = positions.filter(p => p.asset_type === 'OPTION');
        if (!opts.length) return `<div class="list-message">No open option trades</div>`;
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
                        <span class="oc-subname">${esc(p.option_type||'')} $${p.strike?parseFloat(p.strike).toFixed(0):''} · ${fmtDate(p.expiration)}</span>
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

    // ── Symbol Performance (expandable ticker rows) ──

    function renderReconciled(trade) {
        if (trade.type === 'option') {
            const dates = trade.close_date
                ? `${trade.open_date} → ${trade.close_date}` + (trade.days_held != null ? ` (${trade.days_held}d)` : '')
                : trade.status === 'CLOSED' ? `${trade.open_date} → closed`
                : `${trade.open_date} → open`;
            const statusPill = trade.status === 'OPEN'
                ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:rgba(41,98,255,0.12);color:#5B8AF5;font-family:'IBM Plex Mono',monospace;font-weight:600;letter-spacing:0.03em">OPEN</span>`
                : '';
            return `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--tv-border)">
                <div style="display:flex;flex-direction:column;gap:2px;min-width:0;flex:1">
                    <div style="display:flex;align-items:center;gap:6px">
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tv-text);font-weight:500">${esc(trade.strategy)}</span>
                        ${statusPill}
                    </div>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted)">
                        ${trade.strike_low ? '$'+parseFloat(trade.strike).toFixed(0)+'/$'+parseFloat(trade.strike_low).toFixed(0) : '$'+parseFloat(trade.strike).toFixed(0)} ${trade.expiration ? trade.expiration.slice(5) : ''} · ${dates}
                    </span>
                </div>
                <span style="font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:${moneyColor(trade.net)};flex-shrink:0;margin-left:8px">
                    ${signedMoney(trade.net)}
                </span>
            </div>`;
        }
        if (trade.type === 'equity') {
            if (trade.status === 'OPEN' && trade.current_value != null) {
                return `
                <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--tv-border)">
                    <div style="display:flex;flex-direction:column;gap:2px;min-width:0;flex:1">
                        <div style="display:flex;align-items:center;gap:6px">
                            <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tv-text);font-weight:500">${esc(trade.strategy)}</span>
                            <span style="font-size:10px;padding:1px 5px;border-radius:3px;background:rgba(41,98,255,0.12);color:#5B8AF5;font-family:'IBM Plex Mono',monospace;font-weight:600;letter-spacing:0.03em">OPEN</span>
                        </div>
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted)">
                            ${trade.shares} sh · avg $${trade.avg_cost} · now $${trade.current_price}
                        </span>
                    </div>
                    <div style="text-align:right;flex-shrink:0;margin-left:8px">
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:${moneyColor(trade.unrealized_pnl)}">
                            ${signedMoney(trade.unrealized_pnl)}
                        </div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted)">
                            val ${fmtMoney(trade.current_value)}
                        </div>
                    </div>
                </div>`;
            }
            const pnl = trade.realized_pnl ?? 0;
            return `
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--tv-border)">
                <div style="display:flex;flex-direction:column;gap:2px;min-width:0;flex:1">
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--tv-text);font-weight:500">${esc(trade.strategy)}</span>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted)">${trade.ticker || ''}</span>
                </div>
                <span style="font-family:'IBM Plex Mono',monospace;font-size:13px;font-weight:600;color:${moneyColor(pnl)};flex-shrink:0;margin-left:8px">
                    ${signedMoney(pnl)}
                </span>
            </div>`;
        }
        return '';
    }

    function renderSymbolPerf(tickers) {
        if (!tickers.length) return `<div class="list-message">No wheel activity yet</div>`;
        return tickers.map((tk, i) => {
            const isActive  = tk.status === 'ACTIVE';
            const ret       = tk.total_return;
            const retColor  = moneyColor(ret);
            const statusBg  = isActive ? 'rgba(41,98,255,0.12)' : 'rgba(120,123,134,0.12)';
            const statusClr = isActive ? '#5B8AF5' : 'var(--tv-muted)';
            const trades    = tk.reconciled_trades || [];
            const id        = `whl-perf-${i}`;

            const tradeRows = trades.map(renderReconciled).join('');
            const openCount = trades.filter(t => t.status === 'OPEN').length;
            const closedCount = trades.filter(t => t.status === 'CLOSED').length;

            return `
            <div class="overview-card" style="margin:6px 14px;animation:row-in 0.32s ease both;animation-delay:${i*40}ms;cursor:pointer;transition:background 0.15s"
                 onclick="(function(e){var d=document.getElementById('${id}');var c=e.currentTarget.querySelector('.whl-chevron');if(d.style.display==='none'){d.style.display='block';c.style.transform='rotate(90deg)';}else{d.style.display='none';c.style.transform='rotate(0deg)';}})(event)">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0">
                    <div style="display:flex;align-items:center;gap:8px">
                        <span class="whl-chevron" style="display:inline-block;font-size:10px;color:var(--tv-muted);transition:transform 0.15s;transform:rotate(0deg);line-height:1">▶</span>
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:16px;font-weight:600;color:#fff">${esc(tk.underlying||'')}</span>
                        <span style="font-size:11px;padding:2px 6px;border-radius:4px;background:${statusBg};color:${statusClr};font-family:'IBM Plex Mono',monospace;font-weight:600;letter-spacing:0.03em">${esc(tk.status||'')}</span>
                    </div>
                    <div style="text-align:right">
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:14px;font-weight:600;color:${retColor}">
                            ${signedMoney(ret)}
                        </div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--tv-muted)">
                            ${closedCount} closed${openCount ? ' · ' + openCount + ' open' : ''} · prem ${fmtMoney(tk.total_premium)}
                        </div>
                    </div>
                </div>
                <div id="${id}" style="display:none;margin-top:10px;border-top:1px solid var(--tv-border);padding-top:6px" onclick="event.stopPropagation()">
                    ${tradeRows}
                </div>
            </div>`;
        }).join('');
    }

    // ── Main render ──

    function render(el) {
        el.innerHTML = `
            <div class="scanner-header">
                <span class="scanner-title">Wheel Tracker</span>
                <span class="data-freshness-badge" id="whl-badge"></span>
            </div>
            <div id="whl-stats"><div class="list-message loading">Loading…</div></div>

            <div class="section-header" style="padding-top:8px">
                <span class="section-title">Open Holdings</span>
            </div>
            <div id="whl-holdings"><div class="list-message loading">Loading…</div></div>

            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Open Trades</span>
            </div>
            <div id="whl-trades"><div class="list-message loading">Loading…</div></div>

            <div class="section-header" style="padding-top:4px">
                <span class="section-title">Symbol Performance</span>
            </div>
            <div id="whl-perf" style="padding-bottom:16px"><div class="list-message loading">Loading…</div></div>
        `;

        const base = (window.MARKET_INTELLIGENCE_CONFIG?.apiBase) || '';
        Promise.all([
            fetch(`${base}/wheel/stats`).then(r => r.json()),
            fetch(`${base}/wheel/positions`).then(r => r.json()),
            fetch(`${base}/wheel/tickers`).then(r => r.json()),
        ]).then(([stats, posData, tickerData]) => {
            if (!document.getElementById('whl-stats')) return;
            const positions = posData.positions || [];
            document.getElementById('whl-stats').innerHTML    = renderStats(stats);
            document.getElementById('whl-holdings').innerHTML  = renderHoldings(positions);
            document.getElementById('whl-trades').innerHTML    = renderOpenTrades(positions);
            document.getElementById('whl-perf').innerHTML      = renderSymbolPerf(tickerData.tickers || []);
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
