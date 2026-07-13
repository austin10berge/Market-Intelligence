# Market Intelligence — Architecture Reference

> AI context document. Describes the actual deployed system as of April 2026.
> Keep this updated when making structural changes.

---

## Overview

A self-hosted market intelligence system for an options/theta trader. Runs nightly, aggregates
macro sentiment + smart money signals, synthesizes via LLM, and delivers via NTFY push + Discord.
Also exposes a FastAPI backend and static web dashboard for live screener data.

---

## Deployment

Runs as a Docker Compose stack on a Proxmox LXC (hostname: `firefly`).

```
docker-compose.yml defines 4 services:

  api            → FastAPI backend, always-on, port 8000
  dashboard      → nginx static web UI, always-on, port 9009
  discord-bot    → Discord bot + aiohttp callback server, always-on
  pipeline       → Nightly pipeline, profile=pipeline (cron-triggered, exits after run)
```

**Cron trigger for nightly pipeline (on LXC host):**
Redirect stdout+stderr to a log file so failures/tracebacks are captured — `src/main.py`
only configures `logging.basicConfig` (console handler), so nothing is written to disk
without this redirection (`mkdir -p /root/market-intelligence/logs` first):
```
0 19 * * 1-5  docker compose -f /root/market-intelligence/docker-compose.yml run --rm pipeline >> /root/market-intelligence/logs/pipeline.log 2>&1
```

**Dockerfile** uses multi-stage builds. All Python services (api, discord-bot, pipeline) share a
common `base` stage that installs the full `src/` package. The `dashboard` stage is `nginx:alpine`.

**SQLite DB** is mounted as a bind volume at `./data/market_intelligence.db` — persisted on the
LXC host filesystem, shared between `api` and `pipeline` containers.

---

## Python Package — `src/`

Installed as an editable package (`pip install -e .`). All imports use relative package paths.
Python 3.12+ required. Key dependencies: `httpx`, `yfinance`, `fastapi`, `uvicorn`, `pydantic`,
`pydantic-settings`, `google-genai`, `aiohttp`, `discord.py`.

```
src/
├── config.py          — Pydantic Settings, loads from .env. Single `settings` singleton.
├── models.py          — Pydantic models: Signal, ScoredSignal, SignalSource enum, MarketPosture enum
├── db.py              — All SQLite reads/writes. No ORM. WAL mode. See DB Schema below.
├── main.py            — Pipeline orchestrator. Entry point via `python -m src.main`.
├── api/main.py        — FastAPI app. Imports run_pipeline() directly for on-demand scans.
├── fetchers/          — One file per data source, all subclass BaseFetcher
├── processing/        — scorer.py (signal → score) + preprocessor.py (composite + posture)
├── synthesis/         — llm.py (Gemini call) + prompts.py (prompt templates)
├── screener/          — stocks.py + options.py (yfinance + Alpaca, used by API endpoints)
├── notify/            — ntfy.py (primary) + home_assistant.py (fallback)
└── web/               — Static HTML/CSS/JS dashboard + nginx.conf + entrypoint.sh
```

---

## Pipeline Flow (`src/main.py`)

`run_pipeline(output_mode)` is the single entry point. Called by:
- Cron → `python -m src.main` → `main()` → `run_pipeline(output_mode="notify")`
- FastAPI background task → `run_pipeline(output_mode="on-demand")` (skips NTFY, returns dict)

```
Step 1: Fetch all signals in parallel (asyncio.gather across all FETCHERS)
Step 2: Score each signal (+1/-1/0) → store to daily_signals table
        Also captures stock IV snapshots (screen_stocks(persist_history=True))
Step 3: Compute composite score + posture → check_convergence() → build LLM prompt → synthesize
        → store digest to digests table
Step 4: If output_mode=="notify" → send NTFY (primary) → HA fallback
        Always returns structured dict for Discord/API callers
```

---

## Signal Sources (Fetchers)

All fetchers subclass `BaseFetcher` and implement `async fetch() -> Signal | None`.
`safe_fetch()` wraps with exception handling — pipeline never fails due to one bad fetcher.
A shared `httpx.AsyncClient` is reused across all fetchers per pipeline run.

| Source | File | Data | API / Method |
|---|---|---|---|
| Fear & Greed | `fear_greed.py` | CNN F&G score 0–100 | Unofficial CNN endpoint, no key |
| VIX | `vix.py` | VIX spot + term structure (contango/backwardation) | yfinance |
| Put/Call Ratio | `put_call.py` | CBOE equity P/C ratio | CBOE scrape → SPY fallback |
| Sector ETF | `sector_etf.py` | 11 SPDR sector ETF performance, risk-on/off rotation | yfinance |
| GEX | `gex.py` | Gamma Exposure in $B | Estimated from options data |
| Credit Spreads | `credit_spreads.py` | HY/IG spread proxy | FRED API (key required) |
| Liquidity | `liquidity.py` | Net Fed liquidity in $T | FRED API |
| Insider Trading | `insider_trading.py` | Form 4 open-market buys/sells (P/S codes only) | Finnhub API (key required). 12h SQLite cache. |
| Congressional Trades | `congressional_trades.py` | STOCK Act disclosures, House + Senate | housestockwatcher.com + senatestockwatcher.com S3 JSON. No key. 12h SQLite cache. 30d lookback. |
| Unusual Volume | `unusual_volume.py` | Volume vs 20d avg across stock watchlist | yfinance. Threshold: 2x avg, min 500k shares. |

**Insider Trading notes:** Only transaction codes `P` (open-market purchase) and `S` (open-market
sale) are counted. Grants (`A`), option exercises (`M`), gifts (`G`) etc. are ignored as they
carry no sentiment signal.

**Congressional Trades notes:** 30-day lookback to account for the 45-day STOCK Act disclosure lag.
High-profile politicians (Pelosi, Tuberville, etc.) are flagged by name in metadata and marked
`extreme=True` regardless of trade count.

---

## Scoring (`src/processing/scorer.py`)

Each `Signal` → `ScoredSignal` with `score` (+1/-1/0), `direction`, `extreme` flag, `reasoning`.

**Convergence detection** (`check_convergence(scored_signals)`): cross-references
`insider_trading.buy_tickers` with `congressional_trades.buys_by_ticker`. Overlapping tickers
produce `🚨 CONVERGENCE` alerts injected into the LLM prompt as a separate section.

**Composite score** (preprocessor.py): weighted average of all signal scores, range -1.0 to +1.0.
**Market posture**: 7-level enum from "Strongly Bearish" to "Strongly Bullish" based on composite.

---

## Database Schema (`data/market_intelligence.db`)

SQLite, WAL mode. Tables created automatically on first connection via `_ensure_tables()`.

```sql
daily_signals     — One row per (date, source). Stores raw_value, scored_value, direction,
                    extreme, summary, and full metadata as JSON blob.
                    UNIQUE INDEX on (date, source) — pipeline re-runs upsert, not duplicate.

digests           — One row per date. composite_score, posture, llm_summary, full_text.

stock_iv_history  — One row per (date, symbol). ATM IV snapshots for IV Rank calculation.
                    Populated by screen_stocks(persist_history=True) on every pipeline run.

app_config        — Key-value store. Keys:
                      watchlist              → CSP screener ticker list (JSON array)
                      stock_watchlist        → Stock screener ticker list (JSON array)
                      csp_settings           → CSP screener parameters (JSON object)
                      cache_insider_trading  → Cached insider fetch result + cached_at timestamp
                      cache_congressional_trades → Cached congressional fetch result + cached_at
```

**Cache pattern:** `get_insider_cache(max_age_hours)` / `set_insider_cache(data)` in `db.py`.
Fetchers check cache before hitting external APIs. `/insider` Discord command uses 48h fallback.
Both caches invalidate automatically by age — no manual expiry needed.

---

## FastAPI Backend (`src/api/main.py`)

Always-on service, port 8000. Imported directly by the Discord scan trigger (no subprocess).

```
GET  /api/health                 → liveness check
GET  /api/market-posture         → latest digest + signals from DB
GET  /api/screener/stocks        → live stock screener (10min in-memory cache)
GET  /api/screener/csp           → live CSP candidates (10min cache)
GET  /api/screener/leaps         → live LEAPS candidates (10min cache)
GET  /api/watchlist              → CSP watchlist
POST /api/watchlist              → update CSP watchlist
GET  /api/watchlist/stock        → stock watchlist
POST /api/watchlist/stock        → update stock watchlist
GET  /api/settings/csp           → CSP screener parameters
POST /api/settings/csp           → update CSP screener parameters
POST /api/scan/trigger           → [bot-auth] trigger pipeline as background task
GET  /api/scan/history           → [bot-auth] last N digests
GET  /api/insider                → [bot-auth] insider + congressional cache + 7d history
```

Bot-auth endpoints require `x-bot-token: <DISCORD_BOT_SECRET>` header.
On-demand scan POSTs results back to `http://discord-bot:9000/callback` via Docker internal DNS.

---

## Discord Bot (`discord_bot/`)

Separate Python process, runs inside the Docker stack. Loads cogs on startup, syncs slash commands.

```
bot.py                         — Entry point, loads cogs, syncs slash commands
commands/scan.py               — /scan, /scan-history, /scan-status
commands/insider.py            — /insider (view: overview/insiders/congress/convergence, ticker filter)
commands/callback_server.py    — aiohttp server on port 9000, receives POST from FastAPI after scan
utils/embeds.py                — Discord embed builders for scan results
```

**Scan flow:**
```
/scan → bot POSTs to api:8000/api/scan/trigger (x-bot-token header)
     → FastAPI returns 200 immediately, kicks off background task
     → background task runs run_pipeline(output_mode="on-demand")
     → on complete, FastAPI POSTs result to discord-bot:9000/callback
     → callback_server builds embed via embeds.py, sends to channel
```

**`/insider` command** reads from the 48h SQLite cache via `GET /api/insider`. Does not trigger
a live fetch — data is populated by the nightly pipeline. Four views: overview (aggregated totals
+ biggest transaction per ticker), insiders (all Form 4 detail sorted by $value), congress (all
STOCK Act detail), convergence (tickers where both execs + politicians trading same side).

---

## Web Dashboard (`src/web/`)

Plain HTML/CSS/JS — no framework, no build step. Served by nginx inside the `dashboard` container.

**API URL injection:** nginx `entrypoint.sh` generates `config.js` at container startup from
`MARKET_INTELLIGENCE_API_URL` env var. Both `index.html` and `watchlist.html` load `config.js`
before `app.js` so `window.MARKET_INTELLIGENCE_CONFIG.apiBase` is available.
`config.js` is never cached (Cache-Control: no-store). `app.js` and CSS are cached 1h.

The dashboard calls the FastAPI backend directly from the browser — the API URL must be the
LXC's real IP (not `api:8000` which is Docker-internal only). Set `LXC_IP` in `.env` or hardcode
`MARKET_INTELLIGENCE_API_URL` in `docker-compose.yml` for the dashboard service.

---

## Configuration (`.env`)

All settings via Pydantic `Settings` in `src/config.py`. No inline comments on value lines
(pydantic-settings reads them as part of the value).

Key vars:
```
GEMINI_API_KEY          — Gemini 2.0 Flash (LLM synthesis)
FRED_API_KEY            — FRED (credit spreads, liquidity fetchers)
ALPACA_API_KEY/SECRET   — Alpaca market data (stock IV, option chains)
FINNHUB_API_KEY         — Finnhub (insider trades / Form 4)
NTFY_TOPIC/SERVER       — NTFY push notifications
DISCORD_BOT_TOKEN       — Discord bot login token
DISCORD_BOT_SECRET      — Shared secret for bot↔API auth (no inline comments)
DISCORD_CHANNEL_ID      — Default Discord channel ID
LXC_IP                  — LXC host IP for dashboard API URL
DB_PATH                 — SQLite path (default: data/market_intelligence.db)
SCHEDULE_TIME           — Daily run time HH:MM (used for logging only; actual schedule is cron)
```
