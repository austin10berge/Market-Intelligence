Local OHLCV Data Store for CSP Universe Scanner
Problem
Every CSP scan makes three separate rounds of yfinance API calls for ~600 tickers:

Fundamental filter — yf.Ticker(symbol).info for each ticker (market cap, price, beta, IV)
Vol filter — yf.Ticker(symbol).history(period="1mo") for each ticker that passed #1 (RV-20)
Technical conditions — yf.Ticker(symbol).history(period="2y") for each ticker that passed #2
This means a cold scan makes ~1,200+ sequential yfinance HTTP calls, taking 3–6 minutes. Even the Redis cache only delays this — every cache expiry triggers the whole pipeline again.

Proposed Solution
Store 2 years of daily OHLCV data + fundamental snapshots in a local SQLite table (same DB you already have at data/market_intelligence.db), refreshed by a lightweight daily job. The scanner then reads from disk instead of calling yfinance per-ticker.

Why SQLite (not Parquet/CSV/Postgres)?
You already have a SQLite DB with a bind-mounted data/ volume — zero new infrastructure
~600 tickers × 504 bars × 6 columns = ~1.8M rows ≈ 50–80 MB on disk — well within SQLite's sweet spot
WAL mode already enabled — concurrent reads from the API process won't block a writer
Easy to query with date ranges, JOIN to a fundamentals table, etc.
Proposed Changes
Data Layer
[NEW] src/market_data/store.py — OHLCV + fundamentals storage
New module with these responsibilities:

ensure_tables() — Creates two new SQLite tables:
universe_daily_ohlcv — (symbol, date, open, high, low, close, volume) with a UNIQUE(symbol, date) index
universe_fundamentals — (symbol, market_cap_b, price, beta, iv_pct, updated_at) with UNIQUE(symbol) upsert
bulk_upsert_ohlcv(symbol, df) — Takes a yfinance DataFrame and upserts rows (ON CONFLICT DO UPDATE)
bulk_upsert_fundamentals(rows) — Upserts a batch of fundamental snapshots
get_ohlcv(symbol, lookback_days=504) — Returns a pandas DataFrame for a single ticker (reads from SQLite)
get_all_fundamentals() — Returns all fundamental rows as a list of dicts
get_universe_tickers() — Returns the list of tickers in the store
get_store_status() — Returns last update time, ticker count, row count (for the UI/API)
[NEW] src/market_data/refresh.py — Daily refresh job
refresh_universe(full=False) — The main entry point:
Fetches S&P 500 + NASDAQ 100 constituent lists (reuses existing fetch_sp500_tickers() / fetch_nasdaq100_tickers())
For each ticker, calls yf.download() in batches (yfinance supports multi-ticker download) to get OHLCV
On first run (full=True): downloads 2 years of history
On incremental runs: downloads only the last 5 trading days and upserts — this takes ~30 seconds vs 5+ minutes
Also refreshes the fundamentals table (market cap, beta, IV) from yf.Ticker().info in batches
Uses yf.download(tickers, period="2y") which makes 1 bulk HTTP request instead of 600 individual ones — this is the key speedup
[NEW] src/market_data/__init__.py
Expose refresh_universe, get_ohlcv, get_all_fundamentals.

Scanner Integration
[MODIFY] 
csp_scanner.py
Replace the three per-ticker yfinance fetch loops with local DB reads:

apply_fundamental_filter() — Read from universe_fundamentals table instead of calling yf.Ticker().info 600 times
apply_vol_filter() — Read 1-month OHLCV from universe_daily_ohlcv to compute RV-20, instead of yf.Ticker().history(period="1mo")
apply_technical_conditions() — Read 2-year OHLCV from universe_daily_ohlcv for SMA/BB/RSI computation, instead of yf.Ticker().history(period="2y")
Net effect: The scanner goes from ~1,200 HTTP calls over 3–6 minutes to ~600 SQLite reads in <5 seconds (plus the options screener stage which still calls Alpaca for live options pricing).

Refresh Scheduling
[MODIFY] 
docker-compose.yml
Add a market-data-refresh service (under the pipeline profile, or its own refresh profile) that runs python -m src.market_data.refresh daily.

Suggested schedule: Run at 4:30 PM ET (after market close, before your typical evening scan):

# Cron entry (LXC host, ET timezone):
30 16 * * 1-5  docker compose run --rm market-data-refresh
[MODIFY] 
prewarm.py
Optionally add an incremental refresh as the first step of the 9:25 AM pre-warm, so the data is fresh for the trading day.

API & UI
[MODIFY] 
main.py
New endpoint: GET /api/market-data/status — Returns store freshness info (last updated, ticker count, row count) so you can see in the UI when data was last refreshed
New endpoint: POST /api/market-data/refresh — Trigger an incremental refresh on-demand (background task, takes ~30s)
[MODIFY] 
scanner.html / scanner.js
Show a "Data last refreshed: X hours ago" badge in the scanner header (reads from /api/market-data/status)
Optionally add a "Refresh Data" button that triggers /api/market-data/refresh
Performance Comparison
Stage	Current (yfinance per-ticker)	Proposed (local SQLite)
Fundamental filter	~600 .info calls → 2–3 min	1 SQL query → <0.1s
Vol filter (RV-20)	~200 .history(1mo) calls → 1 min	~200 SQL reads → <1s
Technical conditions	~150 .history(2y) calls → 1–2 min	~150 SQL reads → <2s
Total pre-options	3–6 min	<5 sec
Options screener (Alpaca)	~50–80 tickers → 30–60s	unchanged
Full scan	4–7 min	<1 min
User Review Required
IMPORTANT

Daily refresh schedule: I'm proposing 4:30 PM ET on weekdays. Would you prefer a different time? Should weekend refreshes also happen (some data like beta/market-cap can shift with corporate actions)? Answer: 4:30 PM ET on weekdays is perfect. 

IMPORTANT

Initial backfill: The first run needs to download 2 years of OHLCV for ~600 tickers. yf.download() in bulk mode should handle this in ~2–3 minutes. Want me to trigger this automatically on first API startup, or keep it as a manual step? Answer: Trigger automatically on first API startup

Open Questions
NOTE

Fundamental data staleness: Market cap, beta, and IV change daily. The current scanner fetches live data. With a local store, fundamentals would be up to ~24h stale. Is this acceptable? (For a CSP scanner doing EOD snapshots, it almost certainly is — beta and market cap don't move meaningfully intraday.)

NOTE

Stale-data guard: Should the scanner refuse to run (or show a warning) if the local data is older than 48 hours? This would prevent misleading results if the refresh job stops running silently. Answer: Show a warning

Verification Plan
Automated Tests
Unit tests for store.py: upsert, read, dedup, missing-ticker handling
Unit tests for refresh.py: mock yf.download(), verify correct upsert calls
Integration test: run scanner with local data vs. verify same output shape
Manual Verification
Run docker compose run --rm market-data-refresh and verify data/market_intelligence.db grows by ~50–80 MB
Run a scan via the UI and verify it completes in <1 minute instead of 3–6 minutes
Verify the "Data last refreshed" badge appears correctly in the scanner UI