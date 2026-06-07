#!/usr/bin/env bash
# Worker for the SessionEnd recap. Extracts readable text from the session
# transcript, asks `claude -p` for a 3-bullet recap, and appends it to a monthly
# memory log. Runs detached (see session-recap.sh); errors are swallowed so a
# failed recap never disrupts anything.
set -uo pipefail

TRANSCRIPT="$1"
CWD="${2:-/home/dev/workspace/Market-Intelligence}"
MEM_DIR="/home/dev/.claude/projects/-home-dev-workspace-Market-Intelligence/memory/sessions"
LOG="$MEM_DIR/$(date +%Y-%m).md"
mkdir -p "$MEM_DIR"

# Pull user + assistant text out of the jsonl, keep the most recent ~40k chars.
CONVO="$(python3 - "$TRANSCRIPT" <<'PY'
import sys, json
parts = []
try:
    with open(sys.argv[1]) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            else:
                text = ""
            text = text.strip()
            if not text:
                continue
            if role == "user":
                parts.append("USER: " + text)
            elif role == "assistant":
                parts.append("ASSISTANT: " + text)
except FileNotFoundError:
    pass
print("\n\n".join(parts)[-40000:])
PY
)"

# Skip trivial sessions — nothing worth logging.
[[ "${#CONVO}" -lt 200 ]] && exit 0

BRANCH="$(git -C "$CWD" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
GIT_STAT="$(git -C "$CWD" diff --shortstat 2>/dev/null || true)"

PROMPT="You are writing a terse recap for a developer's session-memory log. Below is a coding session transcript (USER/ASSISTANT turns). Write AT MOST 3 bullet points capturing: what changed, why, and anything left unfinished. Be specific — name files/features. Output only the bullets, each starting with '- '. No preamble, no heading.

TRANSCRIPT:
$CONVO"

RECAP="$(MI_RECAP_RUNNING=1 timeout 150 claude -p "$PROMPT" 2>/dev/null || true)"
[[ -z "${RECAP// /}" ]] && exit 0

{
  echo ""
  echo "## $(date '+%Y-%m-%d %H:%M') — ${BRANCH}"
  [[ -n "$GIT_STAT" ]] && echo "_working tree:${GIT_STAT}_"
  echo ""
  echo "$RECAP"
} >>"$LOG"

exit 0
