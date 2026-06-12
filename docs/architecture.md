# Architecture

## System Overview

A self-hosted market intelligence platform for options/theta traders. Three independent concerns share the same Python package (`src/`) and SQLite database:

1. **Nightly pipeline** — fetches macro signals, scores them, synthesizes via Gemini LLM, pushes to NTFY + Discord
2. **FastAPI backend** — always-on REST API (port 8000) for live screener data and pipeline triggering
3. **Discord bot** — slash commands for on-demand scans and insider trade views

## Key Data Flows

**Nightly pipeline** (`python -m src.main` → `run_pipeline()`):
- `asyncio.gather` across all fetchers (`src/fetchers/`) → `scorer.py` → `preprocessor.py` → `llm.py` → NTFY + Discord callback

**On-demand scan** (Discord `/scan` → FastAPI `POST /api/scan/trigger`):
- API kicks off background task → runs `run_pipeline(output_mode="on-demand")` → POSTs result to `discord-bot:9000/callback`

**Screener requests** (browser → `GET /api/screener/csp`):
- Redis cache check (market-hours-aware TTL via `src/cache.py`) → miss → `screen_csp_candidates()` or `run_csp_scan()` → cache set → return

## CSP Scanner Pipeline (`src/screener/csp_scanner.py`)

The broad-universe scanner runs 4 sequential stages:
1. **Universe** — S&P 500 (Wikipedia scrape) + NASDAQ 100 (NASDAQ JSON API)
2. **Fundamental filter** — market cap, price, beta (reads from `universe_fundamentals` table; falls back to live yfinance)
3. **Volatility gate** — IV ≥ threshold primary; RV-20 fallback (reads from `universe_daily_ohlcv`; falls back to yfinance)
4. **Technical conditions** — optional stackable conditions (SMA cross, price vs MA, Bollinger Bands, RSI) + **options screener** (`screen_csp_candidates()` in `src/screener/options.py`)

`ScannerParams` carries all user-configurable parameters. `ScannerParams.cache_key_suffix()` generates a per-param-combination hash used for Redis cache keying.

## Local Market Data Store (`src/market_data/`)

Two SQLite tables prefill scanner Stage 1 and 2 to avoid per-ticker yfinance calls:
- `universe_daily_ohlcv` — daily OHLCV, primary key `(symbol, date)`
- `universe_fundamentals` — market cap, price, beta, IV, primary key `symbol`

`refresh.py` populates these via `yf.download()` bulk requests. Incremental mode fetches the last 5 trading days; `--full` backfills 2 years. The scanner checks `get_store_status()` and emits a warning if data is >48 hours stale.

## Caching Layer (`src/cache.py`)

Redis (via `redis.asyncio`) with market-hours-aware TTLs:
- **Watchlist screeners** (CSP, LEAPS, Stocks): 5-minute TTL during market hours; persists until next open outside hours (max 4h weekends)
- **CSP universe scanner**: fixed 23-hour TTL — designed as an end-of-day snapshot
- **Market posture**: no TTL — explicitly invalidated when the pipeline writes a new digest

Cache misses are always safe: Redis failures are caught and logged, endpoints fall through to live computation.

## Database (`src/db.py`)

SQLite, WAL mode. Tables auto-created on first connection. Schema highlights:
- `daily_signals` — upserted per `(date, source)`, metadata stored as JSON blob
- `digests` — one row per date, composite score + LLM summary
- `stock_iv_history` — ATM IV snapshots for IV Rank calculation
- `app_config` — key-value store for watchlists, CSP settings, and 12h API caches (insider/congressional trades)

## Configuration (`src/config.py`)

Single `settings` singleton via Pydantic `Settings`. All values come from `.env`. **No inline comments on value lines** — pydantic-settings reads them as part of the value.

## Deployment

Docker Compose stack on a Proxmox LXC (`firefly`). Four always-on services: `api`, `dashboard`, `discord-bot`, `redis`. Two cron-triggered one-shot services: `pipeline` (7 PM ET weekdays) and `market-data-refresh` (4:30 PM ET weekdays). `prewarm` (9:25 AM ET) pre-fills Redis cache at market open.

The dashboard (`src/web/`) is plain HTML/CSS/JS — no build step. API URL is injected at nginx container startup via `entrypoint.sh` → `config.js` from the `MARKET_INTELLIGENCE_API_URL` env var.

## Signal Sources

All fetchers subclass `BaseFetcher` (`src/fetchers/base.py`) and implement `async fetch() -> Signal | None`. `safe_fetch()` wraps with exception handling so a single bad source never fails the pipeline. Insider trading and congressional trades results are cached in `app_config` for 12 hours to avoid hammering the APIs.

## Testing

`asyncio_mode = "auto"` — all async tests work without explicit decorators. Tests use real SQLite (temp DB) and `respx` for HTTP mocking. Test fixtures are in `tests/conftest.py`.
