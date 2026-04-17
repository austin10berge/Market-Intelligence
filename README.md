# Market Intelligence

Nightly automated pipeline that aggregates macro, sentiment, and positioning data across multiple sources, synthesizes it via LLM, and delivers a concise evening notification.

Designed for options/theta traders who need a directional read before the next session.

## Architecture

```
[Cron / Manual Trigger]
         │
         ▼
  [Data Fetchers]  ── Fear & Greed, VIX + term structure,
                      Put/Call ratio, Sector ETF performance
         │
         ▼
  [Signal Scorer]  ── +1 bullish / 0 neutral / -1 bearish
  [Preprocessor]   ── Composite score, market posture
         │
         ▼
  [LLM Synthesis]  ── Gemini 2.0 Flash (free tier)
                      ~150 word evening digest
         │
         ▼
  [Notification]   ── NTFY.sh (primary) → iOS push
                      Home Assistant (fallback)
         │
         ▼
  [SQLite Storage] ── Historical signals + digests
```

## Quick Start

```bash
# Clone
git clone git@github.com:YOUR_USER/Market-Intelligence.git
cd Market-Intelligence

# Set up environment
cp .env.example .env
# Edit .env with your API keys

# Install dependencies
pip install -e ".[dev]"

# Run the pipeline
python -m src.main
```

## Docker

```bash
cp .env.example .env
# Edit .env
docker compose up --build
```

## Configuration

All settings are managed via `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `GEMINI_API_KEY` | — | Gemini API key for LLM synthesis |
| `NTFY_TOPIC` | `market-intelligence` | NTFY.sh topic name |
| `NTFY_SERVER` | `https://ntfy.sh` | NTFY server URL |
| `HA_URL` | — | Home Assistant URL (fallback) |
| `HA_TOKEN` | — | HA long-lived access token |
| `SCHEDULE_TIME` | `19:00` | Daily run time (24h format) |
| `DB_PATH` | `data/market_intelligence.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level |

## Phase 1 Data Sources

| Source | Signal | Scoring |
|---|---|---|
| CNN Fear & Greed | 0–100 composite score | <25 bearish, >75 bullish |
| VIX + term structure | Spot price + contango/backwardation | <15 bullish, >25 bearish, backwardation amplifies |
| Equity put/call ratio | Daily ratio + 5-day rolling avg | >1.2 contrarian bullish, <0.7 bearish |
| Sector ETF performance | 11 SPDR sector ETFs | Defensive > cyclical = bearish rotation |

## Future Phases

- **Phase 2**: GEX, credit spreads, TGA liquidity proxy, signal scoring refinement
- **Phase 3**: COT data, SEC Form 4, dark pool prints, NewsAPI
- **Phase 4**: Historical tracking, Grafana dashboard, backtesting

## Project Files

- `DECISIONS.md` — Architectural decision log
- `PROMPTS.md` — LLM prompt iteration history
