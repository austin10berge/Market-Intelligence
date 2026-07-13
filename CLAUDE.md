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
- **Logs without SSH**: Loki is reachable directly from this dev host at `http://10.0.1.25:3100` (no VPN needed) and covers every container in the fleet, including all Market Intelligence services (`market-intelligence-api`, `market-intelligence-pipeline-run-*`, `market-intelligence-prewarm-run-*`, `market-intelligence-discord-bot`, etc. — pipeline/refresh/prewarm runs get a unique `-run-<hash>` suffix per invocation). Query before asking the user to SSH in and tail a file:
  ```bash
  curl -s -G "http://10.0.1.25:3100/loki/api/v1/query_range" \
    --data-urlencode 'query={service_name="market-intelligence-api"}' \
    --data-urlencode "start=$(($(date +%s)-300))000000000" \
    --data-urlencode "end=$(date +%s)000000000" \
    --data-urlencode "limit=50"
  ```
  List available service names: `curl -s "http://10.0.1.25:3100/loki/api/v1/label/service_name/values"`.

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
