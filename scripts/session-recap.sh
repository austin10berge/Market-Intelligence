#!/usr/bin/env bash
# SessionEnd hook: kick off an async session recap, then exit immediately so the
# session (especially a mobile one) closes without waiting on the LLM call.
#
# The actual summarization runs in _session-recap-worker.sh, detached via setsid.
# MI_RECAP_RUNNING guards against recursion: the worker calls `claude -p`, which
# is itself a session whose SessionEnd would otherwise re-trigger this hook.
set -uo pipefail

[[ "${MI_RECAP_RUNNING:-}" == "1" ]] && exit 0

INPUT="$(cat)"
read -r TRANSCRIPT CWD <<<"$(printf '%s' "$INPUT" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("transcript_path", ""), d.get("cwd", ""))
except Exception:
    print(" ")
')"

[[ -z "$TRANSCRIPT" || ! -f "$TRANSCRIPT" ]] && exit 0
[[ -z "$CWD" ]] && CWD="/home/dev/workspace/Market-Intelligence"

WORKER="$CWD/scripts/_session-recap-worker.sh"
[[ -x "$WORKER" ]] || exit 0

setsid env MI_RECAP_RUNNING=1 bash "$WORKER" "$TRANSCRIPT" "$CWD" \
  </dev/null >/dev/null 2>&1 &

exit 0
