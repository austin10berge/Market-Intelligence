# YouTube Summary Pipeline

Daily Python service that monitors YouTube channels, summarizes new uploads with
the `claude -p` CLI (Pro plan, no API key), and posts one Discord embed per
video via a webhook. SQLite tracks posted videos so nothing is double-sent.

## Prerequisites

- Python 3.11+
- Claude Code installed on the host and authenticated as the user that will run
  the timer. Verify with `claude --version` running as that user.
- A Discord webhook URL for the target channel
  (Server Settings → Integrations → Webhooks → New Webhook)

## Setup

```bash
git clone <this repo> /opt/youtube-pipeline
cd /opt/youtube-pipeline/youtube-pipeline
cp .env.example .env
# Edit .env, paste in your DISCORD_WEBHOOK_URL
$EDITOR config.yaml   # add/remove channels under `channels:`
make install
```

`make install` creates the venv, installs deps, copies the systemd units, and
enables the daily timer at 08:00 local.

## Operations

```bash
make run        # one manual run
make dry-run    # fetch + parse but skip claude and Discord
make status     # systemd status + last 50 log lines
make logs       # tail journalctl
make test       # run pytest
make uninstall  # remove systemd units
```

## Configuration

`config.yaml`:

| Key | Meaning |
| --- | --- |
| `channels[].name` | Display name (also used as `--channel` filter) |
| `channels[].url` | Channel URL (`/@handle` form preferred) |
| `pipeline.lookback_hours` | Floor lookback when no prior successful run exists |
| `pipeline.min_duration_seconds` | Below this, treat as a Short and skip |
| `pipeline.max_transcript_chars` | Hard cap on transcript size sent to Claude |
| `pipeline.claude_timeout_seconds` | Per-video `claude -p` timeout |
| `pipeline.inter_call_sleep_seconds` | Sleep between `claude -p` calls (rate-limit cushion) |
| `pipeline.channel_scan_depth` | How many videos per channel to inspect (yt-dlp `--playlist-end`) |

Adding/removing channels is hot — no restart needed; the timer picks up
changes on the next run.

## Testing a single channel

```bash
venv/bin/python main.py --channel "NetworkChuck"
```

Or with no posting / no Claude calls:

```bash
venv/bin/python main.py --channel "NetworkChuck" --dry-run
```

## Wiping state

```bash
rm data/state.db
make run
```

## How dedup works

- `posted_videos` (SQLite) is the source of truth — any video in this table is
  skipped on future runs.
- The "last successful run" timestamp is tracked separately; if the LXC was
  down or a run failed, the next run extends the lookback window automatically.
- `seen_videos` is informational only (forensics), not used for dedup.

## Why not Docker?

`claude -p` authenticates against the per-user state in `~/.claude/`. Wiring
that into a container is awkward (bind-mount the binary, the node runtime, and
the auth directory) for marginal benefit. systemd is simpler here.

## Troubleshooting

**`claude --version` works as your user but fails under systemd**
The service unit sets `HOME=/home/automation_user` and a minimal `PATH`. If
`which claude` returns something outside `/usr/local/bin`, edit
`youtube-pipeline.service` and adjust `PATH`.

**All videos skipped with "no transcript"**
Some channels disable captions entirely. yt-dlp tries both auto-generated and
manual subs in English; if neither exists, the pipeline logs it and moves on.

**Rate-limit errors from `claude -p`**
The pipeline sleeps `inter_call_sleep_seconds` between calls and backs off 60s
on a single rate-limit hit. If you're processing more than ~20 videos per run,
increase the sleep or split channels across multiple runs.

**Pipeline didn't run overnight**
`Persistent=true` in the timer means it catches up on next boot. Check
`systemctl status youtube-pipeline.timer` and `journalctl -u youtube-pipeline`.
