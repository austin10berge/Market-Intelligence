# Morning Brief — Design Spec

**Date:** 2026-09-02  
**Status:** Draft — pending implementation plan

---

## Overview

A daily automated market brief delivered at 8:30 AM PT on trading days. Claude runs non-interactively on the ai-dev LXC, pulls live data, generates a scored opportunity analysis against the user's trading methodology, and delivers a static HTML page + a Discord summary with a link.

The goal is to replace ad-hoc manual market monitoring with a morning brief that tells the user exactly what to watch and what trades to consider that day — before the market opens or shortly after open.

---

## Section 1: Architecture

**Runtime host:** ai-dev LXC (`10.0.1.51`)  
Claude CLI + Schwab MCP are already configured here.

**Execution engine:** `claude --no-interactive -p "$(cat /home/dev/scripts/morning-brief/prompt.md)"` called from a bash cron script.

**Allowed tools for Claude:**
- `mcp__schwab__*` — live quotes, option chains, account positions
- `mcp__rsi__get_rsi` — RSI values (same tool used by Hermes daily finance check)
- `Read` — filesystem access for MI data files
- `Bash` — curl calls to MI API endpoints

**Delivery:**
- HTML brief written to `/var/www/market-intel/brief/index.html` (static file, served by existing nginx)
- Discord summary (5 lines + URL) posted via curl to webhook URL
- Accessible at `https://market.austin10berge.com/brief/` (new nginx `location /brief/` block)

**Cron schedule:** `30 15 * * 1-5` (15:30 UTC = 8:30 AM PT, weekdays only)

**Market-open guard:** The cron script checks the MI API's market hours endpoint (or a hardcoded Federal holiday list) before running. If the market is closed, the script exits without calling Claude.

---

## Section 2: Data Sources

The brief uses three data tiers, pulled fresh each morning:

### Tier 1 — Active Positions
- **Source:** Schwab MCP `get_accounts`
- **Content:** Open wheel positions (short puts, covered calls, LEAPS)
- **Purpose:** "Position Alerts" section — flag contracts needing attention today

### Tier 2 — Conviction Watchlist
- **Source:** `GET http://localhost:PORT/api/watchlist`
- **Content:** The options screener watchlist from the MI web app (user-maintained via v2 UI)
- **Purpose:** "Top Setups" scoring and "Earnings Radar" sections

### Tier 3 — Scanner Hits
- **Source:** `GET http://localhost:PORT/api/screener/stocks` (or filesystem `wheel-candidates/YYYY-MM-DD.json`)
- **Content:** Today's top-ranked CSP candidates from the MI scanner
- **Purpose:** Supplement the watchlist with scanner-surfaced opportunities

### Supporting Data
- **Regime:** `regime-status.json` (read from filesystem, same as Hermes daily finance check)
- **Earnings:** `GET http://localhost:PORT/api/earnings-calendar` — next 7-day earnings for all tickers (new endpoint, see Section 6)
- **RSI:** `mcp__rsi__get_rsi` for each watchlist ticker
- **Quotes + IV:** `mcp__schwab__get_quotes` and `mcp__schwab__get_advanced_option_chain`

---

## Section 3: Brief Content

The HTML brief has five sections, in order:

### 1. Regime Snapshot
Current regime (Bull / Sideways / Bear), VIX level, any drift flag from `regime-status.json`. Two lines at the top of the brief — sets context for everything below.

### 2. Earnings Radar
Any watchlist ticker reporting within 7 days. For each: company name, days until report, current price vs. 52-week range, and a setup framing sentence (e.g., "Post-earnings digestion entry is lower risk than pre-earnings — consider a small LEAPS position after the report."). This section can be empty if no watchlist tickers have upcoming earnings.

### 3. Top 3 Setups Today — DeepSeek-Style Scoring
Claude scores every watchlist ticker (Tier 2) and top scanner hits (Tier 3) using a multi-factor model:

**Input factors per ticker:**
- RSI (from `mcp__rsi__get_rsi`)
- Bollinger Band position (price vs. upper/lower bands — from Schwab price history)
- Consecutive up/down day streak
- IV rank / implied volatility level
- Distance from 50-day and 200-day SMA
- Days until next earnings (from `/api/earnings-calendar`)
- Analyst price target vs. current price

**Scoring:** Claude assigns a setup quality score (1–100) following the DeepSeek firm prompt structure:
- Macro context (regime + VIX) sets the backdrop
- Per-ticker analysis: news/catalyst, technical position, IV environment, valuation
- Score reflects how attractive the ticker is for a new options position TODAY

**Output:** Top 3 ranked setups, each with:
- Ticker + score + one-line signal reason (e.g., "RSI 28, 4 consecutive down days, touching lower Bollinger Band")
- Recommended trade type: **CSP** / **CC** / **LEAPS**
- Specific strike + expiration + target premium (or entry price for LEAPS)
- One-sentence thesis tied to the methodology

**Trade type selection rules (from methodology):**
- CSP: RSI < 35, IV rank > 30%, stock at or below 50-day SMA, no earnings within 3 weeks
- LEAPS: High conviction on multi-quarter thesis, stock pulled back >15% from recent high, RSI < 45
- CC: Existing long position, stock near resistance or RSI > 65

### 4. Position Alerts
Open positions from Schwab that need attention today. Claude checks:
- Short options past 50% max profit → flag for buy-to-close
- Short options within 21 DTE → flag for roll consideration
- Covered calls deep ITM → flag for evaluation
- LEAPS delta below 0.60 → flag for evaluation

Same logic as the Hermes daily finance check "Consider Managing" section.

### 5. Conviction Watchlist Pulse
Significant pre-market or early-session moves in watchlist tickers. Any ticker up or down >3% gets a one-liner (price, % move, volume context). Pulled from `mcp__schwab__get_quotes`.

---

## Section 4: Execution & Delivery

### File Structure (on ai-dev)
```
/home/dev/scripts/morning-brief/
  run.sh              # cron entry point
  prompt.md           # Claude's full instruction prompt
  holidays.txt        # Federal market holiday dates (YYYY-MM-DD, one per line)
```

### `run.sh` Logic
```bash
#!/bin/bash
# 1. Check if today is a market holiday
if grep -qF "$(date +%Y-%m-%d)" /home/dev/scripts/morning-brief/holidays.txt; then
  exit 0
fi

BRIEF_DIR=/var/www/market-intel/brief
SUMMARY_FILE=$BRIEF_DIR/discord-summary.txt

# 2. Run Claude non-interactively
claude --no-interactive \
  -p "$(cat /home/dev/scripts/morning-brief/prompt.md)" \
  --allowedTools "mcp__schwab__*,mcp__rsi__get_rsi,Read,Bash,Write"

# 3. Post Discord summary (Claude writes this file, not stdout)
if [ -f "$SUMMARY_FILE" ]; then
  DISCORD_SUMMARY=$(cat "$SUMMARY_FILE")
  curl -s -X POST "$DISCORD_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "{\"content\": $(echo "$DISCORD_SUMMARY" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"
fi
```

Claude's prompt instructs it to write two files:
1. `/var/www/market-intel/brief/index.html` — full HTML brief
2. `/var/www/market-intel/brief/discord-summary.txt` — 5-line Discord summary + URL

Using files (not stdout) avoids any Claude progress output polluting the Discord message.

### Cron Entry (on ai-dev, `crontab -e`)
```
30 15 * * 1-5 /home/dev/scripts/morning-brief/run.sh >> /var/log/morning-brief.log 2>&1
```

### Nginx Addition (on prod server 10.0.1.21)
Add to the existing MI nginx config:
```nginx
location /brief/ {
    alias /var/www/market-intel/brief/;
    index index.html;
    try_files $uri $uri/ /brief/index.html;
}
```

### HTML Format
Mobile-first card layout (no React, pure HTML + inline CSS). Each section is a collapsible card. Color-coded: green for bullish setups, yellow for neutral, red for alerts. The page includes the generation timestamp and a "Refresh" button that links back to the same URL (no live data — the page is static until the next cron run).

---

## Section 5: Watchlist Maintenance

No new files or processes required. The conviction watchlist is the existing options screener watchlist in the MI v2 web UI, stored in the MI SQLite DB (`app_config` key `'watchlist'`). The user manages it through the web app today.

To add a ticker to the morning brief, add it to the watchlist in the v2 UI. The brief reads it fresh each morning via `GET /api/watchlist`.

---

## Section 6: New MI Code Required

Only one bounded change to the Market Intelligence codebase:

### `/api/earnings-calendar` GET endpoint
**File:** `src/api/main.py`  
**Purpose:** Returns upcoming earnings for all tickers in the next 7 days, using the existing `EarningsCalendarFetcher` (Alpha Vantage, already configured).

**Response shape:**
```json
{
  "upcoming": [
    {"symbol": "CRM", "name": "Salesforce Inc.", "report_date": "2026-09-04", "estimate": "2.44"},
    ...
  ],
  "count": 3,
  "lookahead_days": 7
}
```

The brief prompt calls this endpoint via `curl localhost:PORT/api/earnings-calendar` and cross-references the returned symbols against the watchlist tickers.

---

## Out of Scope

- No real-time data streaming (brief is a daily snapshot)
- No user authentication on the brief URL (same open-access model as dev-mi.austin10berge.com)
- No modification to Hermes jobs (the 4pm daily finance check continues unchanged as a complementary signal)
- No automated trade execution

---

## Open Questions for Implementation

1. **MI API port on ai-dev:** Confirm the local port that `docker compose up` binds (likely 8000 or 8080) so the prompt can call `localhost:PORT/api/...`.
2. **RSI MCP availability on ai-dev:** Confirm `mcp__rsi__get_rsi` is in the ai-dev Claude config (it is in Hermes config — verify ai-dev has it too).
3. **DISCORD_WEBHOOK_URL:** Confirm the env var name or secret file path on ai-dev.
4. **nginx write path:** Confirm `/var/www/market-intel/brief/` is writable by the `dev` user (or adjust path).
