# YouTube Pipeline — Market Intelligence Docker Integration Plan

## Overview

Integrate the YouTube summary pipeline as a scheduled one-shot Docker service
(`youtube-summarizer`) alongside the existing MI stack. It follows the same
pattern as the `pipeline` and `market-data-refresh` services: profile-gated,
cron-triggered, `restart: "no"`.

---

## Key Constraint: `claude -p` Auth in Docker

`claude -p` uses the Pro plan session stored in `~/.claude/` on the host. The
CLI binary is at `/usr/local/bin/claude` (a Node wrapper); it also needs its
Node runtime. The cleanest approach is:

1. Bind-mount `/usr/local/bin/claude` (the binary) into the container.
2. Bind-mount `~/.claude/` from the host into the container's home directory.
3. Set `HOME=/root` (or whichever user runs the container) so the CLI finds
   its session at `~/.claude/`.

The container does **not** need npm/Node installed — the host binary is
self-contained. It just needs to be on `PATH`.

---

## New Docker Service: `youtube-summarizer`

### Profile & Trigger

- `profiles: [youtube]` — excluded from the default `docker compose up` stack.
- Triggered by a cron entry on the LXC host (suggested: 8:00 AM daily, matching
  the previous systemd timer):
  ```
  0 8 * * *  docker compose -f /root/market-intelligence/docker-compose.yml run --rm youtube-summarizer
  ```

### Separate Dockerfile

The youtube pipeline has its own `requirements.txt` (yt-dlp, httpx, pyyaml,
python-dotenv) that differ from the MI `pyproject.toml`. A separate
`Dockerfile.youtube` in the worktree root keeps build contexts clean and avoids
bloating the main image with yt-dlp.

### Volumes

| Mount | Purpose |
|-------|---------|
| `~/.claude:/root/.claude:ro` | Claude CLI auth (read-only) |
| `/usr/local/bin/claude:/usr/local/bin/claude:ro` | Claude CLI binary |
| `./youtube-pipeline/data:/app/data` | SQLite state DB (persists across runs) |
| `./youtube-pipeline/config.yaml:/app/config.yaml:ro` | Channel config (edit without rebuild) |

### Environment Variables

- `DISCORD_YOUTUBE_WEBHOOK_URL` — from `.env`, passed as `DISCORD_WEBHOOK_URL`
  inside the container (matches what `main.py` reads).
- `HOME=/root` — ensures `claude -p` finds `~/.claude/`.

---

## Changes to `docker-compose.yml`

Add one new service block at the end (see Implementation section). No existing
services are modified.

---

## Changes to `.env.example`

Add:
```
# ── YouTube Summary Pipeline ──────────────────────────
DISCORD_YOUTUBE_WEBHOOK_URL=https://discord.com/api/webhooks/XXXXX/YYYYY
```

The outer `.env` in the worktree root is what docker-compose reads; the
`youtube-pipeline/.env.example` (for standalone systemd use) remains unchanged.

---

## Data Persistence

The SQLite state DB lives at `./youtube-pipeline/data/state.db` on the host,
bind-mounted into the container at `/app/data/state.db`. The `Database` class
auto-creates the file and parent directory on first run.

The `youtube-pipeline/data/` directory is created by the first container run.
Add it to `.gitignore` if not already excluded.

---

## What You Need to Configure Before First Run

1. Create/edit `youtube-pipeline/config.yaml` with the channels you want to track.
2. Add your Discord webhook URL to `.env`:
   ```
   DISCORD_YOUTUBE_WEBHOOK_URL=https://discord.com/api/webhooks/...
   ```
3. Verify `claude` auth on the host:
   ```bash
   claude --version
   claude -p "say hello" --output-format json
   ```
4. Add the cron entry (see above).

---

## Running Manually

```bash
# Full run
docker compose run --rm youtube-summarizer

# Dry run (no Claude, no Discord posts)
docker compose run --rm youtube-summarizer python3 main.py --dry-run

# Single channel
docker compose run --rm youtube-summarizer python3 main.py --channel "Fireship"

# Build only
docker compose build youtube-summarizer
```

---

## Why Not Reuse the Existing Dockerfile?

The existing `Dockerfile` is built around `pyproject.toml` and installs the MI
`src/` package. The youtube pipeline is a flat module (no package structure, no
`pyproject.toml`) with a different dependency set. A dedicated
`Dockerfile.youtube` is ~15 lines and keeps both images minimal and
independently cacheable.
