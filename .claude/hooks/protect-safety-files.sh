#!/bin/bash

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Normalize to a path relative to the project root when possible.
REL_PATH="$FILE_PATH"
if [ -n "$CLAUDE_PROJECT_DIR" ]; then
  REL_PATH="${FILE_PATH#$CLAUDE_PROJECT_DIR/}"
fi

# Real-money orders, the kill switch, the live-activation approval and
# gate, exchange credentials, and live config are things the AI must not
# change on its own (CLAUDE.md, "안전 파일"). This hook enforces that at
# the tool level.
PROTECTED_PATTERNS=(
  '^configs/live/'
  '^src/cointrader/live/kill_switch\.py$'
  '^src/cointrader/live/safety_gate\.py$'
  '^src/cointrader/live/approval\.py$'
  '^src/cointrader/funding/bridge\.py$'
  '^src/cointrader/funding/approval\.py$'
  '(^|/)\.env$'
)

for pattern in "${PROTECTED_PATTERNS[@]}"; do
  if echo "$REL_PATH" | grep -qE "$pattern"; then
    echo "BLOCKED: '$REL_PATH' is a safety-critical file (kill switch / live trading gate / broker credentials / live config / real-money fund transfer). Per CLAUDE.md, AI must not modify this without the user's explicit, in-chat confirmation for this specific change. Ask the user to confirm explicitly, or have them make this edit themselves." >&2
    exit 2
  fi
done

exit 0
