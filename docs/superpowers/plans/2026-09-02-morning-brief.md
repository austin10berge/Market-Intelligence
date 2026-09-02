# Morning Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Daily 8:30 AM PT HTML brief served at `https://dev-mi.austin10berge.com/brief/` — Claude scores the user's watchlist against their trading methodology and posts a 5-line Discord summary with a link.

**Architecture:** A bash cron on this machine (dev-mi / ai-dev) runs `claude --no-interactive` with a prompt file at 8:30 AM PT on market days. Claude collects data via Schwab MCP + MI prod API + RSI MCP, scores each watchlist ticker, and writes two files: a static HTML page and a Discord summary. The dashboard Docker container's nginx serves the HTML from a host-mounted directory.

**Tech Stack:** FastAPI (existing MI API), Python 3.12, Docker Compose, bash, `claude` CLI, Schwab MCP, RSI MCP, nginx inside Docker.

**Spec:** `docs/superpowers/specs/2026-09-02-morning-brief-design.md`

## Global Constraints

- Python 3.12; all Python deps run inside Docker — `docker compose run --rm test` for tests
- `ruff` at `~/.local/bin/ruff` — PostToolUse hook auto-formats on save
- Exclude `test_stock_screener.py` from full test runs: `--ignore=tests/test_stock_screener.py`
- Never write to prod directly — deploy changes via the `/deploy` skill (user runs on prod host)
- The brief calls the **prod** MI API (`https://market.austin10berge.com/api`) for watchlist and earnings data — those endpoints must exist on prod before the cron is useful
- Cron runs on this host (dev-mi, `10.0.1.20`), not on prod
- Brief is served at `https://dev-mi.austin10berge.com/brief/` (not prod domain)

---

### Task 1: Add `/api/earnings-calendar` endpoint

**Files:**
- Modify: `src/api/main.py` (append after line ~982)
- Create: `tests/test_earnings_calendar_api.py`

**Interfaces:**
- Consumes: `src.fetchers.earnings_calendar.EarningsCalendarFetcher` (existing)
- Produces: `GET /api/earnings-calendar` → `{"upcoming": [...], "count": int, "lookahead_days": 7}`

Response shape for each item in `upcoming`:
```json
{"symbol": "CRM", "name": "Salesforce Inc.", "report_date": "2026-09-04", "estimate": "2.44"}
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_earnings_calendar_api.py
"""Tests for GET /api/earnings-calendar endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.models import Signal, SignalSource


@pytest.fixture
def upcoming_signal():
    return Signal(
        source=SignalSource.EARNINGS_CALENDAR,
        value=0.0,
        metadata={
            "upcoming": [
                {"symbol": "CRM", "name": "Salesforce Inc.", "report_date": "2026-09-05", "estimate": "2.44"},
            ],
            "count": 1,
            "lookahead_days": 7,
        },
        summary="Earnings Calendar: 1 report(s) in next 7 days",
    )


@pytest.fixture
def empty_signal():
    return Signal(
        source=SignalSource.EARNINGS_CALENDAR,
        value=0.0,
        metadata={"upcoming": [], "count": 0, "lookahead_days": 7},
        summary="Earnings Calendar: no reports in the next 7 days",
    )


async def test_returns_upcoming_earnings(upcoming_signal):
    with patch(
        "src.api.main.EarningsCalendarFetcher.fetch",
        new_callable=AsyncMock,
        return_value=upcoming_signal,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/earnings-calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["lookahead_days"] == 7
    assert data["upcoming"][0]["symbol"] == "CRM"


async def test_returns_empty_when_none_upcoming(empty_signal):
    with patch(
        "src.api.main.EarningsCalendarFetcher.fetch",
        new_callable=AsyncMock,
        return_value=empty_signal,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/earnings-calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["upcoming"] == []


async def test_returns_empty_on_fetch_failure():
    with patch(
        "src.api.main.EarningsCalendarFetcher.fetch",
        new_callable=AsyncMock,
        return_value=None,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/earnings-calendar")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["upcoming"] == []
    assert "error" in data
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose run --rm test python3 -m pytest tests/test_earnings_calendar_api.py -v
```

Expected: FAIL — `ImportError` or `404` (endpoint doesn't exist yet)

- [ ] **Step 3: Add import and endpoint to `src/api/main.py`**

Add the import near the top with other fetcher imports (find a logical grouping):
```python
from ..fetchers.earnings_calendar import EarningsCalendarFetcher
```

Append the endpoint near the end of `src/api/main.py`, before or after the wheel endpoints:

```python
# ── Earnings Calendar ─────────────────────────────────────────────────────────

@app.get("/api/earnings-calendar")
async def get_earnings_calendar():
    """Return upcoming earnings for the next 7 days (Alpha Vantage source)."""
    fetcher = EarningsCalendarFetcher()
    signal = await fetcher.fetch()
    if signal is None:
        return {"upcoming": [], "count": 0, "lookahead_days": 7, "error": "fetch_failed"}
    return signal.metadata
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose run --rm test python3 -m pytest tests/test_earnings_calendar_api.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/api/main.py tests/test_earnings_calendar_api.py
git commit -m "feat: add /api/earnings-calendar endpoint

Wraps existing EarningsCalendarFetcher (Alpha Vantage) to expose upcoming
earnings for the next 7 days. Used by the morning brief cron.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Add `/brief/` serving to dashboard nginx + Docker

**Files:**
- Modify: `src/web/nginx.conf` (add location block)
- Modify: `docker-compose.local.yml` (add volume mount)
- Create: `src/web/brief/` directory (empty, tracked with `.gitkeep`)

**Interfaces:**
- Produces: `https://dev-mi.austin10berge.com/brief/` serves `src/web/brief/index.html`

- [ ] **Step 1: Create the brief directory with a placeholder**

```bash
mkdir -p /home/dev/workspace/Market-Intelligence/src/web/brief
echo "Brief not yet generated." > /home/dev/workspace/Market-Intelligence/src/web/brief/index.html
```

- [ ] **Step 2: Add volume mount to `docker-compose.local.yml`**

In the `x-worktree-dashboard` list (after the `v2` line), add:

```yaml
  - /home/dev/workspace/Market-Intelligence/src/web/brief:/usr/share/nginx/html/brief
```

The full anchor block should look like:
```yaml
x-worktree-dashboard: &worktree-dashboard
  - /home/dev/workspace/Market-Intelligence/src/web/watchlist.html:/usr/share/nginx/html/watchlist.html
  - /home/dev/workspace/Market-Intelligence/src/web/scanner.html:/usr/share/nginx/html/scanner.html
  - /home/dev/workspace/Market-Intelligence/src/web/scanner.js:/usr/share/nginx/html/scanner.js
  - /home/dev/workspace/Market-Intelligence/src/web/v2:/usr/share/nginx/html/v2
  - /home/dev/workspace/Market-Intelligence/src/web/brief:/usr/share/nginx/html/brief
```

- [ ] **Step 3: Add location block to `src/web/nginx.conf`**

Add before the `location /` catch-all (insert after the `/v2/` block, before `location /`):

```nginx
    # Morning brief — static HTML, never cache
    location /brief/ {
        add_header Cache-Control "no-cache, no-store, must-revalidate";
        add_header Pragma "no-cache";
        add_header Expires "0";
    }
```

- [ ] **Step 4: Rebuild and restart the dashboard container**

```bash
cd /home/dev/workspace/Market-Intelligence
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build dashboard
```

- [ ] **Step 5: Verify the brief URL works**

```bash
curl -s https://dev-mi.austin10berge.com/brief/ | head -5
```

Expected: `Brief not yet generated.`

- [ ] **Step 6: Commit**

```bash
git add src/web/nginx.conf src/web/brief/index.html docker-compose.local.yml
git commit -m "feat: add /brief/ location to serve morning brief HTML

Volume-mounts src/web/brief/ into the dashboard nginx container so the
cron-generated brief is served at dev-mi.austin10berge.com/brief/.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Create the cron script and holidays file

**Files:**
- Create: `/home/dev/scripts/morning-brief/run.sh`
- Create: `/home/dev/scripts/morning-brief/holidays.txt`

These files live outside the git repo — they are host scripts on dev-mi.

**Interfaces:**
- Consumes: `/home/dev/scripts/morning-brief/prompt.md` (created in Task 4)
- Consumes: `/home/dev/scripts/morning-brief/discord_webhook_url` (plain text file, one URL)
- Produces: runs `claude --no-interactive`, which writes brief HTML and discord-summary.txt

- [ ] **Step 1: Create the scripts directory**

```bash
mkdir -p /home/dev/scripts/morning-brief
```

- [ ] **Step 2: Create `holidays.txt`**

Write US Federal market holidays for 2026 and 2027 (NYSE observance dates):

```
# US market holidays (NYSE observance) — one date per line (YYYY-MM-DD)
2026-01-01
2026-01-19
2026-02-16
2026-04-03
2026-05-25
2026-07-03
2026-09-07
2026-11-26
2026-12-25
2027-01-01
2027-01-18
2027-02-15
2027-04-02
2027-05-31
2027-07-05
2027-09-06
2027-11-25
2027-12-24
```

- [ ] **Step 3: Create the Discord webhook URL file**

Get the webhook URL for the trading Discord channel (same channel the Hermes daily finance check posts to). Then:

```bash
echo "https://discord.com/api/webhooks/YOUR_WEBHOOK_URL_HERE" > /home/dev/scripts/morning-brief/discord_webhook_url
chmod 600 /home/dev/scripts/morning-brief/discord_webhook_url
```

To find the existing Hermes webhook URL:
```bash
cat ~/.hermes/cron/jobs.json | python3 -c "import json,sys; [print(j.get('name',''), j.get('discord_webhook','')) for j in json.load(sys.stdin)['jobs']]"
```

- [ ] **Step 4: Create `run.sh`**

```bash
#!/bin/bash
# Morning market brief — runs at 8:30 AM PT (15:30 UTC) on weekdays
# Calls Claude non-interactively, writes HTML brief and Discord summary

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIEF_DIR="/home/dev/workspace/Market-Intelligence/src/web/brief"
DISCORD_WEBHOOK_FILE="$SCRIPT_DIR/discord_webhook_url"
LOG_PREFIX="[morning-brief $(date '+%Y-%m-%d %H:%M:%S')]"

echo "$LOG_PREFIX Starting"

# Skip if today is a market holiday
TODAY=$(date +%Y-%m-%d)
if grep -qF "$TODAY" "$SCRIPT_DIR/holidays.txt" 2>/dev/null; then
  echo "$LOG_PREFIX Market holiday — skipping"
  exit 0
fi

# Inject today's date into the prompt
PROMPT=$(sed "s/{{DATE}}/$(date '+%A, %B %-d, %Y')/" "$SCRIPT_DIR/prompt.md")

# Run Claude non-interactively
# Claude will write index.html and discord-summary.txt to $BRIEF_DIR
echo "$LOG_PREFIX Running Claude..."
timeout 600 claude --no-interactive \
  -p "$PROMPT" \
  --allowedTools "mcp__schwab__get_quotes,mcp__schwab__get_accounts,mcp__schwab__get_advanced_option_chain,mcp__schwab__get_option_expiration_chain,mcp__rsi__get_rsi,Read,Write,Bash" \
  2>&1 | tee -a /var/log/morning-brief.log

echo "$LOG_PREFIX Claude finished"

# Post Discord summary
SUMMARY_FILE="$BRIEF_DIR/discord-summary.txt"
if [ -f "$SUMMARY_FILE" ] && [ -f "$DISCORD_WEBHOOK_FILE" ]; then
  WEBHOOK_URL=$(cat "$DISCORD_WEBHOOK_FILE")
  SUMMARY=$(cat "$SUMMARY_FILE")
  PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'content': sys.stdin.read()}))" <<< "$SUMMARY")
  curl -s -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    && echo "$LOG_PREFIX Discord posted" \
    || echo "$LOG_PREFIX Discord post failed"
else
  echo "$LOG_PREFIX No summary file found — Discord skipped"
fi

echo "$LOG_PREFIX Done"
```

- [ ] **Step 5: Make executable**

```bash
chmod +x /home/dev/scripts/morning-brief/run.sh
```

- [ ] **Step 6: Smoke-test the script (without Claude — just check that holiday/path logic works)**

```bash
# Force "today" to a holiday to test the skip path
grep -qF "$(date +%Y-%m-%d)" /home/dev/scripts/morning-brief/holidays.txt \
  && echo "TODAY IS A HOLIDAY (would skip)" \
  || echo "Not a holiday — would proceed"

# Verify BRIEF_DIR exists and is writable
ls -la /home/dev/workspace/Market-Intelligence/src/web/brief/
```

---

### Task 4: Write the prompt file

**Files:**
- Create: `/home/dev/scripts/morning-brief/prompt.md`

This is the core deliverable. It is the complete instruction Claude receives each morning. It must be self-contained — Claude starts with no prior context.

- [ ] **Step 1: Create `prompt.md`**

Write the following to `/home/dev/scripts/morning-brief/prompt.md`:

```markdown
You are generating the daily Morning Market Brief for {{DATE}}.
Current time: 8:30 AM PT. US market has been open for about 1 hour.

You are a financial analyst supporting a trader with this strategy:
- **Cash-Secured Put (CSP):** Sell when RSI < 40, price at/below 50-day SMA, IV > 25%, no earnings within 21 days. Target 30–45 DTE, delta 0.25–0.30.
- **Covered Call (CC):** Sell against existing long shares when RSI > 65, or stock near recent high, or IV elevated. Target 30 DTE, delta 0.25–0.35.
- **LEAPS:** Buy call when stock pulled back >15% from recent 52-week high, RSI < 45, thesis intact. Target Jan 2027 or Jan 2028, delta ~0.70 (near-ATM or 10% OTM).
- **Position sizing:** Small initial entry ($100–500 premium received or cost). Scale in on further weakness.

## Output files

You MUST write exactly two files when done:

1. `/home/dev/workspace/Market-Intelligence/src/web/brief/index.html` — full HTML brief (see HTML format below)
2. `/home/dev/workspace/Market-Intelligence/src/web/brief/discord-summary.txt` — exactly 5 lines (see Discord format below)

## Data collection steps

Run these steps in order. Collect all data before scoring.

### Step 1 — Market regime
Read the file: `/home/dev/workspace/Market-Intelligence/data/regime-status.json`
Extract: regime (bull / sideways / bear), vix level, and any drift or warning flags.
If the file doesn't exist, assume regime = "unknown".

### Step 2 — Watchlist
Run via Bash:
```bash
curl -s https://market.austin10berge.com/api/watchlist
```
Extract the `watchlist` array — this is the list of tickers to score today.

### Step 3 — Earnings in next 7 days
Run via Bash:
```bash
curl -s https://market.austin10berge.com/api/earnings-calendar
```
Note which tickers in the `upcoming` array are also in your watchlist. These get a large earnings penalty in scoring.

### Step 4 — Current quotes for all watchlist tickers
Use `mcp__schwab__get_quotes` with ALL watchlist tickers in a single call.
For each ticker extract: `regularMarketLastPrice`, `regularMarketChangePercent`, `regularMarketVolume`,
`fiftyDayAverage`, `twoHundredDayAverage`, `fiftyTwoWeekLow`, `fiftyTwoWeekHigh`.

### Step 5 — RSI for each watchlist ticker
Call `mcp__rsi__get_rsi` for each ticker. Extract the RSI value (14-period daily).

### Step 6 — Open positions
Call `mcp__schwab__get_accounts`. Extract all open option positions:
type (P/C), underlying ticker, strike, expiration date, current market value, cost basis.

## Scoring each ticker

Compute a setup score (0–100) for every watchlist ticker. Higher = more attractive for a new entry.

**RSI (max 30 pts):**
- RSI ≤ 30 → +30
- RSI 31–40 → +20
- RSI 41–50 → +10
- RSI 51–60 → 0
- RSI 61–70 → −10
- RSI > 70 → −20

**Price vs. moving averages (max 25 pts):**
- Below both 50-day and 200-day SMA → +25
- Below 50-day SMA only → +15
- Between 50-day and 200-day SMA (above 50-day) → +5
- Above both SMAs → 0

**52-week range position (max 20 pts):**
- Bottom 20% of 52-week range → +20
- Bottom 40% → +12
- Bottom 60% → +5
- Top 40% → 0

**Earnings proximity (penalty):**
- Earnings within 7 days → −30
- Earnings 8–21 days away → −15
- Earnings 22+ days away → 0

**Today's price move (±10):**
- Down > 3% → +10
- Down 1–3% → +5
- Up 1–3% → 0
- Up > 3% → −10

**Minimum score threshold:**
- Score ≥ 50: Strong setup — recommend a specific trade
- Score 35–49: Worth watching — mention but don't recommend
- Score < 35: Skip

### Selecting trade type for top setups

For tickers with score ≥ 50, determine trade type:
- RSI < 35 AND large pullback (>15% below 52-week high) → recommend **LEAPS** (buy call) as primary, CSP as secondary
- RSI 35–50 → recommend **CSP** (sell put)
- Existing open position in this ticker → add note about CC or position management instead

### Getting specific strikes for top setups

For the top 3 ranked tickers (score ≥ 50), call `mcp__schwab__get_option_expiration_chain` to see available expirations, then `mcp__schwab__get_advanced_option_chain` to find:
- **CSP:** Strike with delta closest to 0.25 (typically 5–10% OTM), 30–45 DTE expiration
- **LEAPS:** Jan 2027 or Jan 2028 expiration, strike closest to current price (ATM)

## Position alerts

For each open option position from Step 6, check:
- Short option with ≥ 50% premium captured (current value ≤ 50% of original credit) → "Consider closing — 50%+ profit"
- Short option with ≤ 21 DTE → "Consider rolling forward"
- Short option with strike now ITM → "ITM — evaluate roll or close"

## HTML format

Write a complete, self-contained HTML file with NO external dependencies (no CDN, no JavaScript frameworks). Use only inline CSS.

**Page structure:**
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Morning Brief — {{DATE}}</title>
  <style>
    /* Dark theme, mobile-first */
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0f0f0f; color: #e0e0e0; max-width: 640px; margin: 0 auto; padding: 16px; }
    h1 { font-size: 20px; margin-bottom: 4px; }
    .meta { font-size: 12px; color: #777; margin-bottom: 20px; }
    .card { background: #1a1a1a; border-radius: 10px; padding: 16px; margin-bottom: 14px;
            border-left: 4px solid #444; }
    .card.green { border-left-color: #22c55e; }
    .card.yellow { border-left-color: #eab308; }
    .card.red { border-left-color: #ef4444; }
    .card h2 { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.08em;
               margin-bottom: 10px; }
    .badge { display: inline-block; padding: 2px 9px; border-radius: 5px; font-size: 12px;
             font-weight: 700; }
    .badge-bull { background: #14532d; color: #86efac; }
    .badge-bear { background: #7f1d1d; color: #fca5a5; }
    .badge-sideways { background: #78350f; color: #fde68a; }
    .badge-unknown { background: #374151; color: #9ca3af; }
    .setup { padding: 10px 0; border-bottom: 1px solid #252525; }
    .setup:last-child { border-bottom: none; }
    .setup-header { display: flex; justify-content: space-between; align-items: center; }
    .ticker { font-size: 20px; font-weight: 800; }
    .score { font-size: 22px; font-weight: 800; color: #22c55e; }
    .score.mid { color: #eab308; }
    .signal { font-size: 12px; color: #aaa; margin: 5px 0; }
    .trade-box { background: #0a1628; border: 1px solid #1e3a5f; border-radius: 6px;
                 padding: 8px 12px; margin-top: 8px; font-family: monospace; font-size: 13px; }
    .trade-label { color: #60a5fa; font-weight: 700; font-size: 11px; margin-bottom: 3px; }
    .alert-item { padding: 8px 0; border-bottom: 1px solid #252525; font-size: 13px; }
    .alert-item:last-child { border-bottom: none; }
    .pulse-item { display: flex; justify-content: space-between; padding: 6px 0;
                  border-bottom: 1px solid #252525; font-size: 13px; }
    .pulse-item:last-child { border-bottom: none; }
    .up { color: #22c55e; }
    .down { color: #ef4444; }
    .empty { color: #555; font-style: italic; font-size: 13px; }
    .footer { text-align: center; font-size: 11px; color: #444; margin-top: 20px; }
  </style>
</head>
<body>
  <h1>Morning Brief</h1>
  <div class="meta">{{DATE}} · Generated at 8:30 AM PT</div>

  <!-- Section 1: Regime Snapshot -->
  <div class="card [green|yellow|red based on regime]">
    <h2>Market Regime</h2>
    <span class="badge badge-[bull|sideways|bear|unknown]">[REGIME]</span>
    &nbsp; VIX [value] &nbsp; [drift warning if applicable]
  </div>

  <!-- Section 2: Earnings Radar (only show if any watchlist tickers have upcoming earnings) -->
  <div class="card yellow">
    <h2>Earnings Radar</h2>
    [table or list of watchlist tickers with upcoming earnings: ticker, date, days away]
    [or: <p class="empty">No watchlist tickers reporting in the next 7 days.</p>]
  </div>

  <!-- Section 3: Top 3 Setups -->
  <div class="card green">
    <h2>Top Setups Today</h2>
    [For each top setup:]
    <div class="setup">
      <div class="setup-header">
        <span class="ticker">TICKER</span>
        <span class="score [mid if 35–49]">SCORE/100</span>
      </div>
      <div class="signal">RSI: XX · Price: $XX (X% below 50d SMA) · Down X% today</div>
      <div class="trade-box">
        <div class="trade-label">RECOMMENDED TRADE</div>
        CSP: Sell $XX put expiring MMDD for ~$X.XX premium
        [or LEAPS: Buy Jan27 $XXX call @ ~$X.XX]
      </div>
    </div>
    [or: <p class="empty">No strong setups today — all tickers above scoring threshold.</p>]
  </div>

  <!-- Section 4: Position Alerts -->
  <div class="card [red if any alerts, otherwise use default]">
    <h2>Position Alerts</h2>
    [For each alert:]
    <div class="alert-item">⚠️ TICKER $XXX put exp MMDD — [reason]</div>
    [or: <p class="empty">No position alerts today.</p>]
  </div>

  <!-- Section 5: Watchlist Pulse -->
  <div class="card">
    <h2>Watchlist Pulse</h2>
    [For each ticker moving >3% today:]
    <div class="pulse-item">
      <span>TICKER <span class="[up|down]">+X.X%</span></span>
      <span>$XX.XX · Vol: Xm</span>
    </div>
    [or: <p class="empty">No significant moves (>3%) in watchlist today.</p>]
  </div>

  <div class="footer">dev-mi.austin10berge.com/brief/ · {{DATE}} 8:30 AM PT</div>
</body>
</html>
```

Fill in all `[...]` placeholders with real data from your research.

## Discord summary format

Write exactly 5 lines to `/home/dev/workspace/Market-Intelligence/src/web/brief/discord-summary.txt`:

```
📊 Morning Brief — {{DATE}} | [Regime] | VIX [X.X]
🎯 Top setup: [TICKER] ([score]/100) — [trade type]: [one-line reason]
⚠️ Position alerts: [N alerts, one-line summary] OR No alerts today
📅 Earnings this week (watchlist): [TICKER on DATE, ...] OR None
🔗 https://dev-mi.austin10berge.com/brief/
```

Write ONLY these 5 lines — no blank lines, no extra text.
```

- [ ] **Step 2: Verify prompt was written correctly**

```bash
head -5 /home/dev/scripts/morning-brief/prompt.md
wc -l /home/dev/scripts/morning-brief/prompt.md
```

Expected: starts with "You are generating the daily Morning Market Brief" and is > 100 lines.

- [ ] **Step 3: Test date injection**

```bash
sed "s/{{DATE}}/$(date '+%A, %B %-d, %Y')/" /home/dev/scripts/morning-brief/prompt.md | grep "Morning Market Brief"
```

Expected: `You are generating the daily Morning Market Brief for [today's day/date].`

---

### Task 5: Add cron entry and run a first test

**Interfaces:**
- Consumes: `run.sh` (Task 3), `prompt.md` (Task 4)
- Produces: cron entry at 15:30 UTC weekdays; first manual test run to verify end-to-end

- [ ] **Step 1: Add cron entry**

```bash
(crontab -l 2>/dev/null; echo "30 15 * * 1-5 /home/dev/scripts/morning-brief/run.sh >> /var/log/morning-brief.log 2>&1") | crontab -
```

Verify:
```bash
crontab -l | grep morning-brief
```

Expected: `30 15 * * 1-5 /home/dev/scripts/morning-brief/run.sh >> /var/log/morning-brief.log 2>&1`

- [ ] **Step 2: Create the log file**

```bash
sudo touch /var/log/morning-brief.log
sudo chown dev:dev /var/log/morning-brief.log
```

If `/var/log/` is not writable, use `~/morning-brief.log` instead and update `run.sh`.

- [ ] **Step 3: Run the brief manually to test end-to-end**

This will actually call Claude, Schwab MCP, and the MI prod API — it takes 2–5 minutes.

```bash
/home/dev/scripts/morning-brief/run.sh
```

- [ ] **Step 4: Verify the HTML was written**

```bash
ls -la /home/dev/workspace/Market-Intelligence/src/web/brief/
head -20 /home/dev/workspace/Market-Intelligence/src/web/brief/index.html
cat /home/dev/workspace/Market-Intelligence/src/web/brief/discord-summary.txt
```

Expected:
- `index.html` is a complete HTML document (starts with `<!DOCTYPE html>`)
- `discord-summary.txt` has exactly 5 lines

- [ ] **Step 5: Check the brief in a browser**

Open `https://dev-mi.austin10berge.com/brief/` in a browser (or use Playwright):
```bash
# Quick content check
curl -s https://dev-mi.austin10berge.com/brief/ | grep -c "Morning Brief"
```

Expected: returns `1` (title found)

- [ ] **Step 6: Verify Discord message was posted**

Check the configured Discord channel — the 5-line summary should appear. If the Discord webhook file is empty or wrong, update it:
```bash
echo "https://discord.com/api/webhooks/..." > /home/dev/scripts/morning-brief/discord_webhook_url
```

---

### Task 6: Deploy the MI API change to prod

The `/api/earnings-calendar` endpoint (Task 1) must exist on prod before it is useful. The MI API for `watchlist` already exists on prod. This task deploys Task 1 to prod.

- [ ] **Step 1: Push the MI changes to the prod server**

The user runs this manually on the prod server (10.0.1.21). Prepare the exact commands:

```bash
# On prod server (market.austin10berge.com / 10.0.1.21) as the deploy user:
cd /path/to/Market-Intelligence
git pull origin main
docker compose up --build -d api
```

Use the `/deploy` skill for the full deploy procedure.

- [ ] **Step 2: Verify the endpoint is live on prod**

```bash
curl -s https://market.austin10berge.com/api/earnings-calendar | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'count={d[\"count\"]}, lookahead={d[\"lookahead_days\"]}')"
```

Expected: `count=N, lookahead=7`

- [ ] **Step 3: Run the brief again to confirm it now pulls real earnings data**

```bash
/home/dev/scripts/morning-brief/run.sh
```

Check the Earnings Radar section of the brief for real data.

---

## Self-review notes

- **Spec coverage:**
  - Section 1 (Architecture) → Task 3 (run.sh cron script), Task 5 (cron entry)
  - Section 2 (Data Sources) → Task 4 (prompt.md data collection steps)
  - Section 3 (Brief Content + Scoring) → Task 4 (prompt.md scoring rubric + HTML format)
  - Section 4 (Execution & Delivery) → Task 3 (run.sh) + Task 5 (cron entry + log)
  - Section 5 (Watchlist Maintenance) → No code change needed — API already exists
  - Section 6 (Earnings Endpoint) → Task 1 ✓
  - Nginx serving → Task 2 ✓

- **Open items the implementer must confirm before running:**
  1. Discord webhook URL (Task 3, Step 3) — must be populated before first cron run
  2. RSI MCP availability — verify `mcp__rsi__get_rsi` is in the Claude config on this machine: `cat ~/.claude.json | python3 -c "import json,sys; d=json.load(sys.stdin); print([s['name'] for s in d.get('mcpServers',[])])"` — if missing, add it following the same pattern as in `~/.hermes/claude-config`
  3. Schwab MCP token — confirm not expired before first run (see REAUTH.md if needed)
