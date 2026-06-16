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

# Fast path: not a worktree src/ file (only src/ is served by Docker via bind-mount)
[[ -z "$FILE" ]] && exit 0
[[ "$FILE" != *".claude/worktrees/"* ]] && exit 0
[[ "$FILE" != *"/src/"* ]] && exit 0

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
