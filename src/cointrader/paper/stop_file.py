"""Ask a running paper trader to stop by creating a file (used by the windowless launcher, ADR-0057).

On Windows a process without a console cannot be sent Ctrl+C, and killing it skips the clean shutdown that
saves state. The launcher therefore creates `var/paper/STOP`; the trader polls for it and shuts down the same
way as on Ctrl+C. A stale file from an earlier run is removed at start-up so it cannot stop a fresh run.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


def clear(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def watch(path: Path, on_stop: Callable[[], None], *, interval: float = 2.0) -> threading.Thread:
    """Daemon thread: calls `on_stop()` once when `path` appears, then ends."""
    path = Path(path)

    def loop() -> None:
        stopper = threading.Event()
        while not stopper.wait(interval):
            if path.exists():
                on_stop()
                return

    thread = threading.Thread(target=loop, name="paper-stop-file", daemon=True)
    thread.start()
    return thread
