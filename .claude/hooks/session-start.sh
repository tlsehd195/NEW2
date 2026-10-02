#!/bin/bash
set -euo pipefail

# Remote (Claude Code on the web) sessions start from a fresh container,
# so the dev environment has to be reinstalled each time.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# A venv, not a system-wide install: the container's system Python has
# apt-managed packages that break a system-wide `pip install`.
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

.venv/bin/python3 -m pip install --quiet --upgrade pip
.venv/bin/python3 -m pip install --quiet --editable ".[dev]"

echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
