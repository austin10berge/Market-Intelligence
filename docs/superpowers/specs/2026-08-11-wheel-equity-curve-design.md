# Wheel Tracker: Performance vs Benchmark (SPY)

Adds a YTD equity curve chart with SPY benchmark comparison and expandable stats panel to the v2 wheel tracker page, replicating the mlabs blog chart style.

## Data Model

New table `wt_equity_curve` in the existing SQLite DB:

```sql
CREATE TABLE IF NOT EXISTS wt_equity_curve (
    date       TEXT PRIMARY KEY,
    equity     REAL NOT NULL,
    cash       REAL NOT NULL,
    deposits   REAL NOT NULL DEFAULT 0,
    spy_close  REAL
);
```

Each row is one trading day. `equity` = cash + mark-to-market value of open equity positions. `deposits` tracks cumulative external cash inflows for TWR calculation. `spy_close` stored alongside to avoid a second query at read time.

Stats (Sharpe, Sortino, drawdown, annualized yield, avg weekly ROC) are computed on the fly from the curve when the API serves it — ~160 rows for YTD makes this instant.

## Equity Curve Builder

New module `src/wheel_tracker/equity_curve.py` with `rebuild_equity_curve(conn)`:

1. **YTD start** — Jan 1 of current year (or first trade date if later).
2. **Replay trades chronologically** from `wt_trades` — maintains running ledger of cash and open positions (symbol -> quantity, avg cost).
3. **Detect deposits** — hardcode the two known deposit events ($20K Dec 2025, $25K mid-2026) for now; exact dates to be confirmed from Schwab transaction history at implementation time. Plan to auto-detect from Schwab transaction history (TRANSFER/JOURNAL instructions) later.
4. **Price open positions daily** — for each trading day, fetch historical closes via yfinance for held equity tickers. Options contribute their entry premium to cash on open, and realized P&L on close; unrealized option positions between open/close use entry premium as proxy (yfinance doesn't provide historical option prices).
5. **Fetch SPY closes** — single yfinance call for YTD range.
6. **Write to `wt_equity_curve`** — full replace (DELETE + INSERT) on each rebuild.

**Trigger points:**
- Called after each wheel trade sync (existing Schwab import flow).
- Manually via `POST /api/wheel/rebuild-curve`.

## API Endpoint

`GET /api/wheel/equity-curve`

**Response:**
```json
{
  "portfolio_curve": [
    {"date": "2026-01-02", "pct": 0.0},
    {"date": "2026-01-03", "pct": 0.42}
  ],
  "spy_curve": [
    {"date": "2026-01-02", "pct": 0.0},
    {"date": "2026-01-03", "pct": 0.31}
  ],
  "stats": {
    "net_pnl": 11934.73,
    "net_pnl_pct": 15.15,
    "max_drawdown_pct": -9.92,
    "sharpe_ratio": 1.21,
    "sortino_ratio": 1.77,
    "annualized_yield_pct": 25.4,
    "avg_weekly_roc_pct": 0.24
  }
}
```

**Processing:**
1. Read `wt_equity_curve` rows where `date >= Jan 1`.
2. Convert equity to TWR % returns — split at deposit dates, chain sub-period returns.
3. Normalize SPY to % return from first YTD close.
4. Compute stats from daily equity series (logic ported from `backtester/stats.py`):
   - **Sharpe / Sortino** — daily returns, annualized (x sqrt(252)).
   - **Max drawdown** — peak-to-trough on equity series.
   - **Net P&L** — final equity minus (initial capital + deposits).
   - **Annualized yield** — `(1 + total_return) ^ (252 / trading_days) - 1`.
   - **Avg weekly ROC** — mean of Friday-to-Friday % changes.

Returns empty arrays and null stats if curve table has no data.

## Frontend

Adds performance section at top of `wheel.js`, before existing stats cards.

### Chart (full width)

- `lightweight-charts` dual-line area chart, same `makeChart` pattern as `backtester.js`.
- Portfolio: blue area series (`#3b82f6` fill).
- SPY: gray dashed line (`#94a3b8`, lineStyle 2).
- Y-axis: percentage return (0% baseline).
- X-axis: date, Jan through present.
- Legend below chart with colored indicators.
- Title: "Performance vs benchmark (SPY)".
- Height ~220px, optimized for iPhone 12 viewport.

### Stats (expandable, below chart)

- Collapsed by default, chevron expand pattern matching Symbol Performance rows.
- Collapsed header shows headline: e.g. "YTD +15.15%" in green.
- Expands to 2-column grid of `overview-card` styled stat cards:
  - Net P&L ($ and %)
  - Max Drawdown %
  - Sharpe Ratio
  - Sortino Ratio
  - Annualized Yield %
  - Avg Weekly ROC %

### Loading / empty state

- "Loading..." skeleton while fetching.
- "No equity curve data — run a trade sync to generate" if empty.
