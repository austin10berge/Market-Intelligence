# YouTube Pipeline — Market Intelligence Integration Plan

> Hand this to a fresh Claude Code session along with read access to the `austin10berge/market-intelligence` repo. The standalone runner already exists on branch `claude/youtube-summary-pipeline-QrwfC` under `youtube-pipeline/`. This plan integrates it with the MI dashboard and API.

---

## Context

**What already exists** (do not rebuild):
- Standalone runner at `youtube-pipeline/` on branch `claude/youtube-summary-pipeline-QrwfC`.
  - Uses `yt-dlp` for channel listing + transcripts, `claude -p --output-format json` for summarization, Discord webhook for posting.
  - Currently reads channels from `youtube-pipeline/config.yaml` and stores state in `youtube-pipeline/data/state.db`.
  - 27 pytest tests pass. systemd unit + Makefile ready.
- Market Intelligence stack: FastAPI (`src/api/`), SQLite (`src/db.py`), dashboard (`src/web/`, plain HTML/CSS/JS, no build step), Discord bot, Docker Compose.

**What this plan adds:**
- MI dashboard tab to configure channels, view post history, and see last-run status.
- FastAPI endpoints under `/api/youtube/*`.
- New SQLite tables in the MI DB.
- Refactor of the runner to fetch channels via API and report results back to API.

**What stays the same:**
- Runner remains on the LXC host (systemd timer). `claude -p` auth lives in `~/.claude/` and bind-mounting that into a container is more pain than it's worth.
- Discord webhook posting stays in the runner. MI never touches Discord for YouTube.

---

## Architecture

```
┌──────────────┐    GET /api/youtube/channels    ┌──────────────┐
│  systemd     │ ───────────────────────────────▶│  MI FastAPI  │
│  timer       │                                  │  (in docker) │
│  (host)      │ ◀── POST /api/youtube/posts ─── │              │
│              │     POST /api/youtube/runs      └──────┬───────┘
│  runs        │                                         │
│  claude -p   │                                         │ SQLite
│  yt-dlp      │                                         ▼
│              │                                  ┌──────────────┐
│  posts to    │                                  │  market.db   │
│  Discord ────┼─────▶ Discord webhook            │              │
└──────────────┘                                  │  youtube_*   │
                                                  │  tables      │
                                                  └──────┬───────┘
                                                         │
                                                  ┌──────▼───────┐
                                                  │  Dashboard   │
                                                  │  YouTube tab │
                                                  └──────────────┘
```

**Boundary:** The runner is the only thing that knows how to talk to YouTube, Claude, or Discord. MI is purely config + history.

---

## Phase 1 — MI backend: tables, models, endpoints

**Goal:** API + DB layer so config can be read/written and history can be recorded. No UI yet.

### Tasks

1. **Add three tables to `src/db.py`** (auto-created on first connection, same pattern as existing tables):

   ```sql
   CREATE TABLE IF NOT EXISTS youtube_channels (
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     name TEXT NOT NULL UNIQUE,
     url TEXT NOT NULL,
     enabled INTEGER NOT NULL DEFAULT 1,
     created_at TEXT NOT NULL,
     updated_at TEXT NOT NULL
   );

   CREATE TABLE IF NOT EXISTS youtube_posts (
     video_id TEXT PRIMARY KEY,
     channel_name TEXT NOT NULL,
     title TEXT NOT NULL,
     url TEXT NOT NULL,
     summary_text TEXT,
     takeaways_json TEXT,            -- JSON array
     discord_message_id TEXT,
     posted_at TEXT NOT NULL
   );

   CREATE TABLE IF NOT EXISTS youtube_runs (
     id INTEGER PRIMARY KEY AUTOINCREMENT,
     started_at TEXT NOT NULL,
     finished_at TEXT,
     success INTEGER NOT NULL DEFAULT 0,
     videos_found INTEGER NOT NULL DEFAULT 0,
     videos_posted INTEGER NOT NULL DEFAULT 0,
     videos_skipped_no_transcript INTEGER NOT NULL DEFAULT 0,
     videos_skipped_summary_failed INTEGER NOT NULL DEFAULT 0,
     error_message TEXT
   );

   CREATE INDEX IF NOT EXISTS idx_youtube_posts_posted_at ON youtube_posts(posted_at DESC);
   CREATE INDEX IF NOT EXISTS idx_youtube_runs_started_at ON youtube_runs(started_at DESC);
   ```

2. **Create `src/api/youtube.py`** (new FastAPI router, mirror the style of existing routers):

   Endpoints:

   | Method | Path | Purpose |
   | --- | --- | --- |
   | `GET` | `/api/youtube/channels` | List all channels (used by both dashboard and runner) |
   | `POST` | `/api/youtube/channels` | Create channel — body: `{name, url}` |
   | `PATCH` | `/api/youtube/channels/{id}` | Toggle enabled, rename, or change URL |
   | `DELETE` | `/api/youtube/channels/{id}` | Delete (does not delete past posts) |
   | `GET` | `/api/youtube/posts?days=7` | Recent posts, newest first |
   | `POST` | `/api/youtube/posts` | Record a post (called by runner) |
   | `GET` | `/api/youtube/runs/latest` | Most recent run summary |
   | `POST` | `/api/youtube/runs` | Record a run (called by runner) |
   | `GET` | `/api/youtube/status` | Lightweight: `{last_run_at, last_run_success, channel_count, posts_last_7d}` |

   - Pydantic models: `ChannelIn`, `ChannelOut`, `PostIn`, `PostOut`, `RunIn`, `RunOut`.
   - Validate channel URL with a regex matching `youtube.com/@handle`, `youtube.com/c/name`, `youtube.com/channel/UC...`. Reject anything else with 400.
   - `POST /posts` uses `INSERT OR IGNORE` keyed on `video_id` so the runner can be idempotent.
   - `GET /channels` returns `[{id, name, url, enabled}]` only when `enabled=1` unless `?include_disabled=true`. The runner uses the default; the dashboard passes `include_disabled=true`.

3. **Wire the router** into the main FastAPI app — find where existing routers like `csp.py` or `screener.py` are included and add `app.include_router(youtube.router, prefix="/api/youtube", tags=["youtube"])`.

4. **Auth on write endpoints.** If MI already has a token/header check pattern for write endpoints (the scan trigger is the closest parallel), reuse it. If not, gate POST/PATCH/DELETE behind a simple `X-API-Token` header matching `settings.api_token` from `src/config.py`. The runner uses this token; the dashboard fetches it from a `<meta>` tag or `config.js` (whichever pattern dashboard already uses for the screener endpoints).

5. **Tests** in `tests/test_youtube_api.py`:
   - CRUD lifecycle on channels.
   - Duplicate channel name → 409.
   - Invalid YouTube URL → 400.
   - `POST /posts` with same `video_id` twice → second call is a no-op, returns 200.
   - `GET /status` returns sane shape with zero channels.

   Use the same SQLite-temp-DB pattern as existing API tests in `tests/conftest.py`.

---

## Phase 2 — Dashboard tab

**Goal:** A "YouTube" tab in the dashboard for managing channels and viewing history.

The MI dashboard is plain HTML/CSS/JS — no build step. Match the existing pattern for how tabs are added (look at how the CSP scanner or Watchlist tabs are wired in `src/web/`).

### Tasks

1. **Add the tab.** Find the nav/tab definition in the main dashboard HTML and add a "YouTube" entry pointing to a new section or new page (whichever the existing tabs use).

2. **UI layout** — three sections stacked:

   **a. Last run status** (top, compact):
   ```
   ┌────────────────────────────────────────────────────┐
   │ Last run: 2026-05-29 08:00 UTC · ✓ success         │
   │ 3 channels · 5 found · 4 posted · 1 skipped        │
   └────────────────────────────────────────────────────┘
   ```
   Fed by `GET /api/youtube/status`. Refreshes when the tab is opened.

   **b. Channels** (middle, editable):
   ```
   Channels                                    [+ Add]
   ┌────────────────────────────────────────────────────┐
   │ ☑ Fireship          @Fireship          [edit] [×]  │
   │ ☑ NetworkChuck      @NetworkChuck      [edit] [×]  │
   │ ☐ ColdFusion        @ColdFusion        [edit] [×]  │  (disabled)
   └────────────────────────────────────────────────────┘
   ```
   - Checkbox toggles `enabled` (PATCH).
   - "Edit" opens an inline form to change name/URL.
   - "×" deletes after a confirm dialog.
   - "+ Add" opens an inline form: name + URL, validates the URL pattern client-side before POST.

   **c. Recent posts** (bottom, scrolling list, last 7 days):
   ```
   Recent Posts (last 7 days)
   ┌────────────────────────────────────────────────────┐
   │ ▸ How Linux really works                            │
   │   NetworkChuck · 2026-05-28 · [▶ YouTube] [💬 Discord]
   │   Summary preview (first 200 chars)…                │
   ├────────────────────────────────────────────────────┤
   │ ▸ Next.js just changed forever                      │
   │   Fireship · 2026-05-27 · [▶ YouTube] [💬 Discord]  │
   │   …                                                 │
   └────────────────────────────────────────────────────┘
   ```
   - Each row is collapsed to summary preview; click to expand to full summary + takeaways.
   - "Discord" link opens the message (`https://discord.com/channels/{guild_id}/{channel_id}/{message_id}` — needs guild/channel IDs in config; if not available, just hide the button).

3. **JS module** `src/web/youtube.js`:
   - `loadChannels()`, `addChannel(name, url)`, `toggleChannel(id, enabled)`, `deleteChannel(id)`, `loadPosts()`, `loadStatus()`.
   - On API errors, show an inline error banner — don't `alert()`.
   - URL validation regex client-side (same pattern as backend) so users get instant feedback.

4. **Styling** — reuse existing CSS classes from the screener/watchlist tabs. Don't introduce a new design system.

5. **No "Run now" button in v1.** The runner is on the host and the API container can't trigger systemctl. Document in the tab: "Pipeline runs daily at 08:00. Use `systemctl start youtube-pipeline.service` on the host to trigger manually."

---

## Phase 3 — Runner refactor

**Goal:** Swap `config.yaml` channels for API-fetched channels and report results back to MI.

### Tasks

1. **Add `mi_client.py`** to `youtube-pipeline/`:
   ```python
   class MIClient:
       def __init__(self, base_url: str, token: str | None, timeout: float = 15.0): ...
       def get_channels(self) -> list[Channel]: ...        # GET /api/youtube/channels
       def record_post(self, summary: Summary, message_id: str) -> None: ...  # POST /api/youtube/posts
       def start_run(self) -> int: ...                     # POST /api/youtube/runs (started_at only) → returns id
       def finish_run(self, run_id: int, *, success, counters: dict, error: str | None) -> None: ...
       def list_recent_post_ids(self, days: int = 7) -> set[str]: ...  # for cross-process dedup
   ```
   - Use `httpx` (already a dep).
   - Token from env: `MI_API_TOKEN`. Base URL from env: `MI_API_URL` (default `http://localhost:8000`).
   - On network failure, retry once after 5s (mirror Discord poster).
   - If the API is unreachable, log loudly but **don't crash**: fall back to local SQLite state so a daily run still succeeds even if the dashboard is down.

2. **Update `config.yaml`** — remove the `channels:` section. Add:
   ```yaml
   mi_api:
     url: "http://localhost:8000"
     token_env: "MI_API_TOKEN"
   ```
   Keep `pipeline:` block as-is.

3. **Update `main.py`:**
   - Replace `config["channels"]` with `mi_client.get_channels()` at the start of `run()`.
   - Wrap the channel fetch in try/except — if it fails AND there's no local cached channel list (new file: `data/channels.cache.json`), abort with a clear error. If we have a cache, use it and log a warning.
   - On successful fetch, write the cache.
   - After each successful `record_post()` on the local DB, also call `mi_client.record_post()`.
   - Replace `db.start_run()` / `db.finish_run()` with `mi_client.start_run()` / `mi_client.finish_run()` calls **in addition to** the local ones (local stays as a fallback record).
   - Dedup: union `db.has_been_posted(video_id)` with `mi_client.list_recent_post_ids()` so a manual entry in the MI DB also skips the video. Cache the API response for the run.

4. **Update `.env.example`:**
   ```
   DISCORD_WEBHOOK_URL=...
   MI_API_URL=http://localhost:8000
   MI_API_TOKEN=...
   ```

5. **Update tests** — add `tests/test_mi_client.py` with `respx` (or `httpx.MockTransport`) mocking the MI endpoints. Confirm the fallback-to-local behavior when the API returns 500.

6. **Update `README.md`** in `youtube-pipeline/`:
   - Channels are managed via MI dashboard, not `config.yaml`.
   - Pipeline reads from MI on each run; falls back to cache if MI is down.
   - Setup adds `MI_API_TOKEN` step.

---

## Phase 4 — Deploy and wire up

### Tasks

1. **Migration.** First run after deploy:
   - User opens YouTube tab → sees empty channel list.
   - User adds channels manually (or runs a one-shot script that POSTs the current `youtube-pipeline/config.yaml` entries to `/api/youtube/channels`).
   - Provide a small helper: `youtube-pipeline/scripts/migrate_channels_to_mi.py` that reads the old YAML and POSTs each entry.

2. **Networking.** The runner runs on the LXC host; the MI API runs in a Docker container with port 8000 published. The runner hits `http://localhost:8000` — this works because the MI Compose stack maps `8000:8000` on the host. Confirm with `curl http://localhost:8000/api/youtube/channels` from the host before deploying the runner changes.

3. **Backfill.** Optionally import historical posts from `youtube-pipeline/data/state.db` into MI's `youtube_posts` table. Same migration script can do this — read all rows from local `posted_videos`, POST to `/api/youtube/posts`.

4. **Update CLAUDE.md** in the MI repo with one sentence about the YouTube integration and a pointer to the standalone runner repo path on the LXC.

5. **Sanity end-to-end:**
   - Add a channel via the dashboard.
   - `systemctl start youtube-pipeline.service` on the host.
   - Watch logs: `journalctl -u youtube-pipeline -f`.
   - Confirm Discord post appears.
   - Confirm post shows up in the YouTube tab within a few seconds (refresh).
   - Confirm "Last run" status updates.

---

## Notes for the implementing session

- The runner code on branch `claude/youtube-summary-pipeline-QrwfC` is the source of truth — don't rewrite `summarizer.py`, `transcripts.py`, `channels.py`, `discord_poster.py`. Only `main.py`, `config.yaml`, `db.py`, `.env.example`, and tests change in Phase 3.
- The MI dashboard does **not** use a JS framework — no React/Vue. Match the existing vanilla-JS pattern. If you find existing tabs using a specific JS module loader pattern, follow it exactly.
- The MI SQLite file is at `data/market.db` (relative to project root) and mounted at `/app/data/market.db` inside containers. The host runner reads `http://localhost:8000`, not the DB file directly — keep the API as the only boundary.
- All write endpoints (`POST`, `PATCH`, `DELETE`) need auth. The dashboard either reads the token from an env-injected `config.js` (the screener does this — confirm the pattern) or is gated by being on the same origin. Don't ship without some form of write protection.
- The pipeline already filters Shorts and livestreams in `channels.py`. Don't duplicate that filter on the MI side.
- Stick to two deployment artifacts: MI Compose stack (unchanged structure, +1 router, +1 tab, +3 tables), and the host systemd timer (unchanged unit, refactored Python). Don't try to fold the runner into Compose.
- Track work on a new branch off `main`, e.g. `claude/youtube-mi-integration`. The runner-side changes can go on the existing `claude/youtube-summary-pipeline-QrwfC` branch or be merged forward — implementer's call.
