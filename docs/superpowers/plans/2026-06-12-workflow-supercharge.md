# Workflow Supercharge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the #1 session friction (worktree-vs-main deployment confusion) with a deterministic hook, trim CLAUDE.md to a token-efficient core, replace 196 stale permission entries with 32 intentional ones, and add two workflow skills.

**Architecture:** Seven files created or modified across three concerns: context (CLAUDE.md + docs/architecture.md), enforcement (worktree-guard hook + settings.local.json), and workflow (three project skills). No new dependencies; all changes are configuration, shell script, and markdown.

**Tech Stack:** Bash (hook script), JSON (settings), Markdown (CLAUDE.md, skills), Python 3 (JSON parsing in hook)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `CLAUDE.md` | Rewrite | Trimmed core: env, commands, topology, caching, MCP rules |
| `docs/architecture.md` | Create | Full architecture detail (moved from CLAUDE.md) |
| `scripts/worktree-guard-hook.sh` | Create | PreToolUse: blocks edits to unmounted worktree paths |
| `.claude/settings.local.json` | Rewrite | 32-entry allow list + PreToolUse/PostToolUse/SessionEnd hooks |
| `.claude/skills/deploy/SKILL.md` | Create | /deploy checklist skill |
| `.claude/skills/env-check/SKILL.md` | Create | /env-check orientation snapshot skill |
| `.claude/skills/verify/SKILL.md` | Create | /verify project override — iPhone 12 viewport only |

---

## Task 1: Create `docs/architecture.md`

Extract the Architecture section from CLAUDE.md verbatim into a new file so CLAUDE.md can reference it with `@docs/architecture.md`.

**Files:**
- Create: `docs/architecture.md`

- [ ] **Step 1: Create the file**

```markdown
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
```

- [ ] **Step 2: Verify the file exists and is non-empty**

Run: `wc -l docs/architecture.md`
Expected: 70+ lines

---

## Task 2: Rewrite `CLAUDE.md`

Replace the current 145-line file with a trimmed core (~70 lines) that adds the three new sections and references `@docs/architecture.md` for details.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite CLAUDE.md with the new content**

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environments

- **Dev** (this machine): https://dev-mi.austin10berge.com — always use this URL when testing or checking API/dashboard status
- **Prod**: https://market.austin10berge.com — separate host (10.0.1.21), do not target unless explicitly asked

## Environment

- **Python**: 3.12, no local virtualenv. All Python deps live inside Docker images — run tests and pipeline via `docker compose`. Bare `python -m ...` on the host will fail.
- **ruff** at `~/.local/bin/ruff` (add to PATH). A `PostToolUse` hook auto-formats every edited `.py` file — no manual format step needed.
- Exclude `test_stock_screener.py` with `--ignore=tests/test_stock_screener.py` when running the full suite.

## Commands

```bash
# Lint / format
~/.local/bin/ruff check src/ tests/
~/.local/bin/ruff format src/ tests/

# Docker — run all always-on services (api, dashboard, discord-bot, redis)
docker compose up --build

# Tests via Docker
docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py
docker compose run --rm test python3 -m pytest tests/test_market_data_store.py -v

# Nightly pipeline
docker compose run --rm pipeline

# Market data refresh
docker compose run --rm market-data-refresh                                               # incremental (last 5 days)
docker compose run --rm market-data-refresh python3 -m src.market_data.refresh --full    # 2-year backfill

# Cache pre-warm
docker compose run --rm prewarm
```

## Deployment Topology

- Docker serves from the **main workspace** (`/home/dev/workspace/Market-Intelligence/src/`). Files in git worktrees (`.claude/worktrees/<id>/`) are **not served** unless `docker-compose.local.yml` explicitly mounts that worktree's `src/`.
- When debugging a live issue: edits must land in the main workspace. Check `docker-compose.local.yml` `x-worktree-src` before assuming a worktree is mounted.
- **Production is a separate host** (10.0.1.21). `dev-mi.austin10berge.com` is dev only. Diagnose prod bugs against the PROD API (`market.austin10berge.com`), not local containers.
- Claude has no SSH/prod access. Prepare exact commands for the user to run manually.

## Worktree → Dev Dashboard Testing

To test worktree changes against the running stack, `docker-compose.local.yml` bind-mounts the worktree's `src/` via the `x-worktree-src` anchor. **Update that path when switching worktrees.**

```bash
# One-time image build (only when pyproject.toml changes)
docker compose -f docker-compose.yml -f docker-compose.local.yml build pipeline api

# Pipeline run using worktree source
docker compose -f docker-compose.yml -f docker-compose.local.yml run --rm pipeline

# Restart API to pick up changes (no rebuild needed for Python-only edits)
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --no-build api
```

## Browser Testing

The frontend (`src/web/`) is JS-rendered — verify UI changes with the **Playwright MCP** (`mcp__playwright__browser_*`), not `curl`/`WebFetch`. Playwright drives a registered headless Chromium.

- Test against **dev** (`https://dev-mi.austin10berge.com`) — public, no auth needed.
- Drive real interactions (set filters, click **Run Scan**, wait for results) — bare page load shows only the empty form.
- Use `browser_snapshot` (accessibility tree) to assert state; `browser_take_screenshot` for visual proof.
- If `mcp__playwright__*` tools are absent, the server was added mid-session — start a fresh session.

## Caching Rules

When a value appears stuck or a fix has no visible effect: suspect **stale Redis cache or stale Docker image layer first**. Prefer a permanent image rebuild over container-copy hacks — the user will reject non-permanent workarounds.

## MCP & Tooling

- Home Assistant dashboard: use the `ha-mcp` server (already registered). Not `hass-mcp`, not curl.
- Playwright: registered MCP server — don't hardcode node_modules paths or assume system installs.

## Architecture

For full architecture detail see `@docs/architecture.md`.
```

- [ ] **Step 2: Verify line count is under 100**

Run: `wc -l CLAUDE.md`
Expected: under 100 lines

---

## Task 3: Create `scripts/worktree-guard-hook.sh`

PreToolUse hook that blocks file edits to git worktrees that are not currently mounted in `docker-compose.local.yml`. Exit 2 tells Claude Code to abort the tool call and show the message.

**Files:**
- Create: `scripts/worktree-guard-hook.sh`

- [ ] **Step 1: Write the hook script**

```bash
#!/usr/bin/env bash
# PreToolUse hook: block edits to worktree files that Docker doesn't serve.
# Exit 2 = block the tool call (Claude Code shows stderr to the model).
# Exit 0 = allow through.
set -uo pipefail

INPUT="$(cat)"

FILE="$(printf '%s' "$INPUT" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")
')"

# Fast path: not a worktree file
[[ -z "$FILE" ]] && exit 0
[[ "$FILE" != *".claude/worktrees/"* ]] && exit 0

# Extract the worktree ID (the directory immediately after "worktrees/")
WORKTREE_ID="$(python3 -c "
import sys, re
m = re.search(r'\.claude/worktrees/([^/]+)/', sys.stdin.read())
print(m.group(1) if m else '')
" <<< "$FILE")"

[[ -z "$WORKTREE_ID" ]] && exit 0

# Check whether docker-compose.local.yml mounts this worktree
COMPOSE_LOCAL="/home/dev/workspace/Market-Intelligence/docker-compose.local.yml"
if [[ -f "$COMPOSE_LOCAL" ]] && grep -q "$WORKTREE_ID" "$COMPOSE_LOCAL" 2>/dev/null; then
    exit 0  # Worktree IS mounted — allow the edit
fi

cat >&2 << EOF
BLOCKED: Editing a file in worktree '$WORKTREE_ID' but docker-compose.local.yml
is not mounting that worktree's src/. Docker serves from the main workspace.

Options:
  a) Apply this edit to the equivalent path under
     /home/dev/workspace/Market-Intelligence/src/
  b) Update docker-compose.local.yml x-worktree-src to point to this worktree first.
EOF
exit 2
```

- [ ] **Step 2: Make the script executable**

Run: `chmod +x scripts/worktree-guard-hook.sh`

- [ ] **Step 3: Test the blocking path (unmounted worktree)**

Run:
```bash
echo '{"tool_input":{"file_path":"/home/dev/workspace/Market-Intelligence/.claude/worktrees/agent-abc123def/src/fetchers/news.py"}}' \
  | bash scripts/worktree-guard-hook.sh
echo "Exit: $?"
```

Expected: exit code `2`, stderr contains `BLOCKED: Editing a file in worktree 'agent-abc123def'`

- [ ] **Step 4: Test the fast path (main workspace file)**

Run:
```bash
echo '{"tool_input":{"file_path":"/home/dev/workspace/Market-Intelligence/src/fetchers/news.py"}}' \
  | bash scripts/worktree-guard-hook.sh
echo "Exit: $?"
```

Expected: exit code `0`, no output

- [ ] **Step 5: Verify the allow path logic manually**

The allow-path code is `grep -q "$WORKTREE_ID" "$COMPOSE_LOCAL"`. Confirm it would pass for the current config (which mounts the main workspace, not any worktree):

```bash
grep "worktrees" docker-compose.local.yml && echo "worktree IS in local compose" || echo "no worktree mounted (expected)"
```

Expected: `no worktree mounted (expected)` — confirming the current setup correctly blocks any worktree edit.

---

## Task 4: Rewrite `.claude/settings.local.json`

Replace the 196-entry allow list with 32 intentional entries, wire in the PreToolUse worktree-guard hook, preserve PostToolUse ruff format and SessionEnd recap hooks.

**Files:**
- Modify: `.claude/settings.local.json`

- [ ] **Step 1: Write the new settings.local.json**

```json
{
  "permissions": {
    "allow": [
      "Bash(docker compose *)",
      "Bash(docker exec *)",
      "Bash(docker run *)",
      "Bash(git status *)",
      "Bash(git diff *)",
      "Bash(git log *)",
      "Bash(git show *)",
      "Bash(git branch *)",
      "Bash(git remote *)",
      "Bash(git rev-parse *)",
      "Bash(git worktree *)",
      "Bash(git stash list)",
      "Bash(git ls-files *)",
      "Bash(curl *)",
      "Bash(python3 -m *)",
      "Bash(python3 -c *)",
      "Bash(node *)",
      "Bash(npx *)",
      "Bash(npm *)",
      "Bash(~/.local/bin/ruff *)",
      "Bash(claude mcp *)",
      "Bash(claude doctor *)",
      "Bash(crontab -l)",
      "Read(//home/dev/.claude/**)",
      "Read(//tmp/**)",
      "WebFetch(domain:www.home-assistant.io)",
      "WebFetch(domain:github.com)",
      "WebSearch",
      "Skill(update-config)",
      "Skill(update-config:*)",
      "mcp__playwright__*",
      "mcp__home-assistant__*"
    ]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "/home/dev/workspace/Market-Intelligence/scripts/worktree-guard-hook.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/home/dev/workspace/Market-Intelligence/scripts/ruff-format-hook.sh"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/home/dev/workspace/Market-Intelligence/scripts/session-recap.sh"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Validate JSON syntax**

Run: `python3 -m json.tool .claude/settings.local.json > /dev/null && echo "Valid JSON"`
Expected: `Valid JSON`

- [ ] **Step 3: Verify allow list count**

Run: `python3 -c "import json; d=json.load(open('.claude/settings.local.json')); print(len(d['permissions']['allow']), 'entries')"`
Expected: `32 entries`

- [ ] **Step 4: Verify all three hook types are present**

Run: `python3 -c "import json; d=json.load(open('.claude/settings.local.json')); print(list(d['hooks'].keys()))"`
Expected: `['PreToolUse', 'PostToolUse', 'SessionEnd']`

---

## Task 5: Create `/deploy` skill

**Files:**
- Create: `.claude/skills/deploy/SKILL.md`

- [ ] **Step 1: Create the skills directory and skill file**

```markdown
# /deploy

Run this checklist before any deployment. Prevents the "edit has no visible effect" class of bugs.

## Steps

### 1. Verify the workspace

Run `git diff --name-only HEAD` (or `git diff --name-only` for unstaged). Confirm all changed files are under `/home/dev/workspace/Market-Intelligence/src/` — the main workspace that Docker serves.

If any changed file is under `.claude/worktrees/`, STOP. Tell the user: the edit is in a git worktree. Unless `docker-compose.local.yml` mounts that worktree, Docker will never serve it.

### 2. Check docker-compose.local.yml

Run: `grep -A3 "x-worktree-src" docker-compose.local.yml`

If the output shows a `.claude/worktrees/<id>/` path (not the main workspace), ask the user: "A worktree is currently mounted. Is this intentional for this deployment?"

### 3. Determine rebuild scope

- **Python-only changes** (`.py` files, no `pyproject.toml`): no image rebuild needed. Just restart the container.
- **JS/HTML changes** (dashboard frontend): no rebuild. Nginx serves the bind-mounted files directly.
- **`pyproject.toml` changes** or new pip dependencies: image rebuild required.

Tell the user which applies.

### 4. Output the exact commands for the user to run

Do NOT run these yourself — the user executes them on the host.

```bash
# If rebuild is needed:
docker compose build api          # or: pipeline, dashboard
docker compose up -d --no-build api

# If no rebuild (Python or frontend changes only):
docker compose up -d --no-build api
```

Replace `api` with `pipeline` or `dashboard` as appropriate for what changed.

### 5. Identify Redis cache to bust (if applicable)

Check what endpoint or feature changed. If a cached API response is involved, output the specific DEL command:

```bash
# List all cache keys to find the right one:
docker compose exec redis redis-cli KEYS "*"

# Delete a specific key (replace <key> with the actual key):
docker compose exec redis redis-cli DEL <key>
```

Common keys: `screener:csp:*`, `screener:leaps:*`, `market-posture`

### 6. Confirm visually after the user runs the commands

Once the user confirms they've run the commands, use Playwright to navigate to the affected page and take a screenshot:

```
mcp__playwright__browser_resize({ width: 390, height: 844 })
mcp__playwright__browser_navigate({ url: "https://dev-mi.austin10berge.com" })
mcp__playwright__browser_take_screenshot({})
```

Report what the screenshot shows. Declare success only if the expected change is visibly present.
```

- [ ] **Step 2: Verify the file was created**

Run: `wc -l .claude/skills/deploy/SKILL.md`
Expected: 60+ lines

---

## Task 6: Create `/env-check` skill

**Files:**
- Create: `.claude/skills/env-check/SKILL.md`

- [ ] **Step 1: Create the skill file**

```markdown
# /env-check

Run at the start of any debugging session to get a live orientation snapshot before touching anything.

## Steps

Run these four commands and report results in the summary format below.

### 1. Container status

```bash
docker compose ps
```

### 2. API URL the dashboard is hitting

```bash
curl -s https://dev-mi.austin10berge.com/config.js
```

Look for `MARKET_INTELLIGENCE_API_URL` in the output. If it points to `localhost` or `10.0.1.x`, the dashboard is hitting dev. If it points to `market.austin10berge.com`, it's hitting prod.

### 3. Git state

```bash
git status --short
git branch --show-current
```

### 4. Redis uptime

```bash
docker compose exec redis redis-cli INFO server 2>/dev/null | grep uptime_in_seconds || echo "redis not running"
```

Convert seconds to hours/minutes for the summary.

### 5. Detect if running in a worktree

Check if `$PWD` contains `.claude/worktrees/`. If yes, extract the worktree ID and note it. If no, confirm main workspace.

```bash
echo "$PWD" | grep -o '\.claude/worktrees/[^/]*' || echo "main workspace"
```

## Output Format

Report as a clean table:

```
Environment:   dev  (dev-mi.austin10berge.com)
API target:    http://api:8000  [or whatever config.js shows]
Containers:    api ✓   dashboard ✓   discord-bot ✓   redis ✓
Git:           main, clean  [or: 3 uncommitted files]
Redis uptime:  4h 12m  [or: not running]
Workspace:     main workspace  [or: worktree agent-abc123def]
```

After the table, note any anomalies: a container that's down, uncommitted changes, Redis just restarted (may explain stale data), or a worktree mounted that isn't the main workspace.
```

- [ ] **Step 2: Verify the file was created**

Run: `wc -l .claude/skills/env-check/SKILL.md`
Expected: 55+ lines

---

## Task 7: Create `/verify` project override skill

The built-in `/verify` skill checks for a project-level override at `.claude/skills/verify/SKILL.md` first. This override locks Playwright to iPhone 12 viewport (390×844) for all verifications in this project.

**Files:**
- Create: `.claude/skills/verify/SKILL.md`

- [ ] **Step 1: Create the skill file**

```markdown
# /verify — Market Intelligence Project Override

Always use **iPhone 12 viewport (390×844)** for this project. This is the primary form factor for the dashboard.

## Steps

### 1. Set viewport

```
mcp__playwright__browser_resize({ width: 390, height: 844 })
```

### 2. Navigate

Go to `https://dev-mi.austin10berge.com` unless the user specifies a different URL or path.

```
mcp__playwright__browser_navigate({ url: "https://dev-mi.austin10berge.com" })
```

### 3. Drive real interactions

A bare page load shows only the empty form or skeleton UI. Drive the actual flow being verified:

- **Watchlist tab**: wait for ticker data to load, check that prices and sparklines render
- **Scanner tab**: set filters, click Run Scan, wait for results
- **Overview tab**: check VIX/GEX/breadth bars render
- **Backtester tab**: load a strategy, check the equity curve renders
- **Analysis tab**: select a ticker, confirm TradingView widget loads

Use `mcp__playwright__browser_snapshot` to assert the accessibility tree state, then `mcp__playwright__browser_take_screenshot` for the visual artifact.

### 4. Check the console

```
mcp__playwright__browser_console_messages({})
```

Flag any errors or failed network requests.

### 5. Declare pass or fail

Based on what you actually observe in the screenshot and snapshot — not just whether the page loaded. Be specific: "The CSP candidates table rendered with 12 rows" or "The equity curve chart is blank — likely a data-loading error."
```

- [ ] **Step 2: Verify the file was created**

Run: `wc -l .claude/skills/verify/SKILL.md`
Expected: 45+ lines

---

## Task 8: End-to-End Smoke Test

Verify all seven changes work together before declaring done.

**Files:** (read-only verification, no changes)

- [ ] **Step 1: Confirm CLAUDE.md is concise**

Run: `wc -l CLAUDE.md`
Expected: under 100 lines

- [ ] **Step 2: Confirm architecture.md exists and is complete**

Run: `grep -c "##" docs/architecture.md`
Expected: 8+ section headers

- [ ] **Step 3: Confirm hook script is executable and blocks correctly**

Run:
```bash
echo '{"tool_input":{"file_path":"/home/dev/workspace/Market-Intelligence/.claude/worktrees/agent-fakeid999/src/main.py"}}' \
  | bash scripts/worktree-guard-hook.sh 2>&1
echo "Exit: $?"
```
Expected: stderr contains `BLOCKED`, exit code `2`

- [ ] **Step 4: Confirm settings.local.json is valid and has correct structure**

Run:
```bash
python3 -c "
import json
d = json.load(open('.claude/settings.local.json'))
allow = d['permissions']['allow']
hooks = d['hooks']
print(f'Allow entries: {len(allow)}')
print(f'Hook types: {sorted(hooks.keys())}')
print(f'PreToolUse matcher: {hooks[\"PreToolUse\"][0][\"matcher\"]}')
"
```
Expected:
```
Allow entries: 32
Hook types: ['PostToolUse', 'PreToolUse', 'SessionEnd']
PreToolUse matcher: Edit|Write|MultiEdit
```

- [ ] **Step 5: Confirm all three skills exist**

Run:
```bash
for skill in deploy env-check verify; do
  echo -n "$skill: "
  [[ -f ".claude/skills/$skill/SKILL.md" ]] && echo "✓" || echo "MISSING"
done
```
Expected: all three show `✓`

- [ ] **Step 6: Check CLAUDE.md references architecture.md correctly**

Run: `grep "architecture" CLAUDE.md`
Expected: line containing `@docs/architecture.md`
