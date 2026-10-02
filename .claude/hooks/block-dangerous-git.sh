#!/bin/bash

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Note: plain "git push" is intentionally NOT blocked — pushing to the
# designated work branch is normal, required workflow in this repo. Only
# force-pushes and other irreversible/history-destroying operations are.
DANGEROUS_PATTERNS=(
  "git reset --hard"
  "git clean -fd"
  "git clean -f"
  "git branch -D"
  "git checkout \."
  "git restore \."
  "push --force"
  "push -f"
  "reset --hard"
)

for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qE "$pattern"; then
    echo "BLOCKED: '$COMMAND' matches dangerous pattern '$pattern'. The user has prevented you from doing this." >&2
    exit 2
  fi
done

exit 0
