#!/bin/bash
# Blocks merging a PR while one of this checkout's ADR numbers is already
# taken on main or claimed first by another pushed branch (ADR-0221).
# Only a real collision blocks; if the check cannot run, the merge goes ahead.
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
[ -f scripts/adr_number.py ] || exit 0
OUT=$(python3 scripts/adr_number.py check 2>&1)
STATUS=$?
[ $STATUS -eq 0 ] && exit 0
if [ $STATUS -eq 1 ] && echo "$OUT" | grep -q "adr_number.py renumber"; then
  echo "BLOCKED: ADR number collision -- renumber, commit, push, then merge." >&2
  echo "$OUT" >&2
  exit 2
fi
echo "ADR number check could not run (merge not blocked): $OUT" >&2
exit 0
