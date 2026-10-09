"""One paper trader per state directory.

Two processes writing `var/paper/` at once would corrupt the state files, so the trader takes an exclusive
OS lock on a file for as long as it runs. The OS drops the lock when the process ends, even if it crashes,
so there is no stale lock to clean up.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Optional


def acquire(path: Path) -> Optional[IO[bytes]]:
    """Return the open lock file (keep it referenced while running), or None if another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ImportError:  # Windows
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle
