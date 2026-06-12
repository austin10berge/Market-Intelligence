# Workflow Supercharge Design

**Date:** 2026-06-12  
**Goal:** Close the #1 session friction (dev-vs-prod environment confusion), cut input token cost, and add two workflow skills — while keeping the setup minimal and maintainable.

---

## Problem Summary

From 19 analyzed sessions, three recurring failure modes account for most friction:

1. **Wrong environment edits** — Claude edits files in git worktrees that Docker never serves, producing zero visible effect until the fix is re-applied to the main workspace.
2. **Dev-vs-prod misdiagnosis** — Claude debugs local dev containers when the bug lives in production.
3. **Wrong tool rabbit holes** — Claude probes wrong MCP servers or improvises curl access instead of using registered tools.

The root cause is the same in all three: CLAUDE.md lacks explicit deployment topology context, and nothing mechanically stops the bad path.

---

## Approach: Layered Defense (A)

Four interlocking layers:

| Layer | Mechanism | Reliability |
|-------|-----------|-------------|
| 1. Context | CLAUDE.md trimmed core + new Deployment Topology section | ~75% (advisory) |
| 2. Enforcement | PreToolUse worktree-guard hook | 100% (deterministic) |
| 3. Permissions | Clean 32-entry allow list replacing 196 entries | Always |
| 4. Workflow | `/deploy` + `/env-check` skills, `/verify` project override | On-demand |

---

## 1. CLAUDE.md Restructuring

### Target structure (~145 lines)

Keep in CLAUDE.md (always loaded, every session):
- `## Environments` — dev/prod URLs (2 lines)
- `## Environment` — Python/ruff/test note (condensed, ~8 lines)
- `## Commands` — bash commands unchanged
- **`## Deployment Topology`** — NEW (see content below)
- `## Worktree → Dev Dashboard Testing` — condensed to key facts (~10 lines)
- `## Browser Testing` — condensed (~7 lines), Playwright MCP reference
- **`## Caching Rules`** — NEW (3 lines)
- **`## MCP & Tooling`** — NEW (3 lines)
- One-liner: `For architecture details see @docs/architecture.md`

Move to `docs/architecture.md` (loaded on demand via `@` import):
- System Overview, Key Data Flows, CSP Scanner Pipeline, Local Market Data Store, Caching Layer (technical), Database, Configuration, Deployment detail, Signal Sources, Testing note

### New section content

**`## Deployment Topology`**
```
- Docker serves from the MAIN workspace (/home/dev/workspace/Market-Intelligence/src/).
  Files in git worktrees (.claude/worktrees/<id>/) are NOT served unless
  docker-compose.local.yml explicitly mounts that worktree's src/.
- When debugging a live issue: edits must land in the main workspace.
  Check docker-compose.local.yml x-worktree-src before assuming a worktree is mounted.
- Production is a separate host (10.0.1.21) — dev-mi.austin10berge.com is dev only.
  Diagnose prod bugs against the PROD API (market.austin10berge.com), not local containers.
- Claude has no SSH/prod access. Prepare exact commands for the user to run manually.
```

**`## Caching Rules`**
```
- When a value appears stuck or a fix has no visible effect: suspect stale Redis cache or
  stale Docker image layer first. Prefer a permanent image rebuild over container-copy hacks.
  The user will reject non-permanent workarounds.
```

**`## MCP & Tooling`**
```
- Home Assistant dashboard: use the `ha-mcp` server (registered). Not `hass-mcp`, not curl.
- Playwright is registered as an MCP server. Do not hardcode node_modules paths.
  If mcp__playwright__* tools are absent, start a fresh session (server added mid-session).
```

---

## 2. PreToolUse Enforcement Hook

### File: `scripts/worktree-guard-hook.sh`

**Trigger:** `PreToolUse` on tools `Edit | Write | MultiEdit`

**Logic:**
1. Parse `tool_input.file_path` from stdin JSON
2. If path does NOT contain `.claude/worktrees/` → exit 0 (fast path, no overhead)
3. Extract the worktree ID from the path (`worktrees/<id>/...`)
4. Read `docker-compose.local.yml` and check if its `x-worktree-src` anchor contains that worktree ID
5. If the worktree IS mounted in docker-compose.local.yml → exit 0 (allow)
6. If NOT mounted → exit 2 with message:

```
BLOCKED: Editing a file in worktree '<id>' but docker-compose.local.yml
is not mounting that worktree's src/. Docker serves from the main workspace.

Options:
  a) Apply this edit to the equivalent path under
     /home/dev/workspace/Market-Intelligence/src/
  b) Update docker-compose.local.yml x-worktree-src to point to this worktree first.
```

**Wire-up:** Add entry to `settings.local.json` hooks:
```json
{
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
    ]
  }
}
```

Note: SessionEnd hook remains in settings.local.json separately (already wired).

---

## 3. `settings.local.json` Allow List

Replace the current 196-entry list with 32 intentional entries:

```json
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
```

**Gated (prompt each time):** `git commit`, `git push`, `git pull`, `git fetch`, `git checkout`, `git merge`, `git reset`, `git add`, `git rebase`, `git restore`

---

## 4. Skills

### `/deploy` skill

**Location:** `.claude/skills/deploy/SKILL.md`  
**Trigger:** User runs `/deploy` before any deployment step.

**Checklist:**
1. Confirm edited files are in the main workspace — print the actual paths that were changed (`git diff --name-only`)
2. Check `docker-compose.local.yml` x-worktree-src — if a worktree is mounted, flag it and confirm with user
3. Determine rebuild scope: Python-only changes → no rebuild needed; `pyproject.toml` or new deps → `docker compose build <service>`
4. Output exact commands for the user to run (do not run them):
   - Rebuild if needed: `docker compose build api` / `docker compose build pipeline`
   - Restart: `docker compose up -d --no-build api` (or relevant service)
5. Identify stale Redis keys: check what endpoint was changed and output the `redis-cli DEL <key>` command if relevant
6. After user confirms they've run the commands: use `mcp__playwright__browser_navigate` to load the affected page and `browser_take_screenshot` as proof of effect

### `/env-check` skill

**Location:** `.claude/skills/env-check/SKILL.md`  
**Trigger:** User runs `/env-check` at the start of any debugging session.

**Steps:**
1. `docker compose ps` — print which containers are running and their health
2. `curl -s https://dev-mi.austin10berge.com/config.js` — confirm which API URL the dashboard is hitting
3. `git status --short` + `git branch --show-current` — are we on main? uncommitted changes?
4. `docker compose exec redis redis-cli INFO server | grep uptime` — when did Redis last restart?
5. Output a clean summary table:

```
Environment:   dev (dev-mi.austin10berge.com → api:8000)
Containers:    api ✓  dashboard ✓  discord-bot ✓  redis ✓
Git:           main, 2 uncommitted files
Redis uptime:  4h 12m
Workspace:     /home/dev/workspace/Market-Intelligence (main workspace, not worktree)
```

### `/verify` project override skill

**Location:** `.claude/skills/verify/SKILL.md`

The built-in `/verify` skill checks for a project-level skill first and uses it if present. Create a project override that specifies:
- Use **iPhone 12 viewport only**: `390×844` (set via `mcp__playwright__browser_resize` before navigation)
- Navigate to the dev URL (`https://dev-mi.austin10berge.com`) unless the user specifies otherwise
- Drive real interactions (don't just load the page — set filters, click through)
- Capture a screenshot as the verification artifact
- Report pass/fail based on what the screenshot shows, not just whether the page loaded

---

## File Checklist

| File | Action |
|------|--------|
| `CLAUDE.md` | Rewrite: trim to ~145 lines, add 3 new sections |
| `docs/architecture.md` | Create: move architecture content from CLAUDE.md |
| `scripts/worktree-guard-hook.sh` | Create: PreToolUse enforcement hook |
| `.claude/settings.local.json` | Update: add PreToolUse hook, replace allow list |
| `.claude/skills/deploy/SKILL.md` | Create: /deploy checklist skill |
| `.claude/skills/env-check/SKILL.md` | Create: /env-check orientation skill |
| `.claude/skills/verify/SKILL.md` | Create: project override — iPhone 12 viewport only |

---

## Out of Scope

- Visual regression baselines (no current screenshot baseline exists — worth a separate session)
- Stop hook verification (adds latency; revisit if env-check + deploy skill don't close the gap)
- SSH/prod deploy access (infrastructure change, not a Claude Code config change)
- FactSet/Morningstar/S&P MCP auth (separate concern)
