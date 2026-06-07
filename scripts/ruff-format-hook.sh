#!/usr/bin/env bash
# PostToolUse hook: auto-format Python files after Claude edits them.
# Reads the hook JSON on stdin, extracts the edited file path, and runs ruff
# format on it. Silent no-op for non-Python files or if ruff is missing.
set -uo pipefail

RUFF="$HOME/.local/bin/ruff"

INPUT="$(cat)"
FILE="$(printf '%s' "$INPUT" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")
')"

[[ -z "$FILE" || "${FILE##*.}" != "py" || ! -f "$FILE" ]] && exit 0
[[ -x "$RUFF" ]] || exit 0

"$RUFF" format "$FILE" >/dev/null 2>&1 || true
exit 0
