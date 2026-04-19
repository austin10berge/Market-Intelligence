# Changelog

## 2026-04-17

### Stock Screener Volatility Work

- Added a stock screener volatility layer using Alpaca options data plus `yfinance` stock history.
- Fixed the stock table loading bug where the stock section kept the `loading` class and blinked indefinitely.
- Fixed the stock table grid layout so added columns render in the correct position.
- Added persistent SQLite storage for stock ATM IV history in `stock_iv_history`.
- Added a 60-trading-day historical ATM IV backfill job using Alpaca option contracts plus Alpaca historical option bars.
- Added `IV Rank` to the stock screener and kept `IV/RV20` alongside it.
- Added tooltips in the UI for:
  - `IV Rank`, showing how many history points support the rank
  - `IV/RV20`, explaining the interpretation

### Current Stock Table Columns

#### Existing price/performance columns

- `Price`
  - Latest close from `yfinance` stock history.

- `1D %`
  - `(current close - previous close) / previous close * 100`

- `1W %`
  - `(current close - close 5 trading days ago) / close 5 trading days ago * 100`

- `1M %`
  - `(current close - close 21 trading days ago) / close 21 trading days ago * 100`
  - If fewer than 21 trading days are available, falls back to the oldest available close in the fetched history window.

- `P/E`
  - `trailingPE` from `yfinance` ticker info.

- `Beta`
  - `beta` from `yfinance` ticker info.

### Volatility Columns

#### `ATM IV`

- Not shown directly in the table today, but computed in the backend.
- Definition:
  - Use Alpaca option chain snapshots for the stock.
  - Restrict to expirations with `30-45 DTE`.
  - Restrict to strikes near spot price.
  - Pick the expiration closest to target `37 DTE`.
  - Pick the strike closest to current stock price.
  - Use both call and put when available.
  - If Alpaca provides per-contract IV directly, use it.
  - If Alpaca does not provide IV, derive IV by solving Black-Scholes from the option midpoint or last trade.
  - Average the call/put IV values for that selected ATM point.

#### `RV20`

- Not shown directly in the table today, but computed in the backend.
- Definition:
  - Compute daily close-to-close returns from stock history.
  - Take the most recent 20 trading days of returns.
  - Compute the sample standard deviation of those returns.
  - Annualize using `sqrt(252)`.
  - Convert to percent.

- Formula:
  - `RV20 = stddev(last 20 daily returns) * sqrt(252) * 100`

#### `IV/RV20`

- Displayed in the stock screener as `IV/RV20`.
- Definition:
  - `ATM IV / RV20`

- Formula:
  - `IV/RV20 = ATM IV / RV20`

- Interpretation:
  - Higher value means options are pricing in more movement than the stock has recently realized.
  - Lower value means implied volatility is closer to, or below, recent realized volatility.

#### `IV Rank`

- Displayed in the stock screener as `IV Rank`.
- Uses the stored `ATM IV` history in SQLite.
- Definition:
  - Compare current `ATM IV` to the low/high range of the stored ATM IV series.
  - Lookback limit is currently `252` rows max, but practical history depends on what has been stored/backfilled.
  - Rank is not shown until there are at least `20` ATM IV observations for the symbol.

- Formula:
  - `IV Rank = (current ATM IV - min ATM IV history) / (max ATM IV history - min ATM IV history) * 100`

- Guardrails:
  - If there are fewer than `20` history points, show `N/A`.
  - If the stored high and low are identical, show `N/A`.

- Important note:
  - This is not intended to exactly match Thinkorswim.
  - Current implementation ranks a reconstructed `30-45 DTE ATM IV` series, not a broker-native proprietary underlying IV series.

### Persistence and Backfill

#### Daily persistence

- The nightly pipeline now captures one ATM IV snapshot per stock each run.
- The stock screener API path also persists today’s ATM IV when it successfully computes it.

#### Backfill job

- Added `src/backfill_stock_iv.py`.
- Current job backfills approximately `60` recent trading days.
- It works by:
  - pulling underlying stock close history from `yfinance`
  - discovering matching Alpaca option contracts for the relevant `30-45 DTE` windows
  - fetching Alpaca daily option bars for the selected contracts
  - reconstructing one ATM IV snapshot per trading day
  - storing those snapshots in `stock_iv_history`

### Database Changes

- Added SQLite table:
  - `stock_iv_history(date, symbol, atm_iv, created_at)`

- Added unique index:
  - one ATM IV snapshot per `date + symbol`

### Known Limitations

- `IV Rank` may differ from Thinkorswim or other brokers because:
  - current IV source is our reconstructed Alpaca-based ATM IV, not a broker-native underlying IV metric
  - lookback history is currently limited by stored and backfilled data
  - current backfill is 60 trading days, not full 52-week history

- Alpaca `indicative` option chain responses do not always include direct IV fields, so the system sometimes derives IV from option midpoints using Black-Scholes.

- Large ETF/mega-cap option universes can require more contract pagination during backfill, so the backfill job narrows contract discovery by strike range around the stock’s historical trading range.
