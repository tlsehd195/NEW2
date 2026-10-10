#!/usr/bin/env python3
"""Starts, stops and checks the whole paper-trading stack without any black console windows (ADR-0057).

    pythonw scripts/launcher.py start    # paper trader + data server + Next.js page, browser opens when ready
    pythonw scripts/launcher.py stop     # asks the paper trader to save state and stop, then ends everything
    python  scripts/launcher.py status   # which parts are running, and how fresh the paper trader's state is

Everything runs in the background; each part writes its own log under var/logs/. Starting twice is safe: parts that
already run are left alone (the paper trader also refuses a second copy by itself). Read-only dashboard + the
paper trader only; nothing here places a real order or touches the live-trading safety files.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cointrader.paper import single_instance  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402

DATA_PORT, PAGE_PORT = 8765, 3000
PAGE_URL = f"http://127.0.0.1:{PAGE_PORT}"
LOGS = ROOT / "var" / "logs"
PIDS = ROOT / "var" / "launcher" / "pids.json"
NEXT_DIR = ROOT / "dashboard-next"
WINDOWS = os.name == "nt"


def log(message: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "launcher.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")


def popup(title: str, message: str, *, wait: bool = True) -> None:
    """A small message box on Windows (there is no console to print to); elsewhere just the log."""
    log(f"popup: {title}: {message}")
    if not WINDOWS:
        return
    try:
        import ctypes

        show = lambda: ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)  # noqa: E731
        if wait:
            show()
        else:
            threading.Thread(target=show, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def _app_browser() -> str | None:
    """Edge first (ships with Windows), then Chrome; None means fall back to the default browser."""
    candidates = []
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    candidates.sort(key=lambda p: "msedge" not in p.name)  # stable: Edge before Chrome
    for path in candidates:
        if path.exists():
            return str(path)
    return shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chrome")


def open_window() -> None:
    """Own window without address bar or tabs (browser app mode); closing it leaves the trader running."""
    exe = _app_browser()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={PAGE_URL}", "--window-size=1280,860"])
            log(f"opened app window with {exe}")
            return
        except OSError as exc:
            log(f"app window failed ({exc}); using default browser")
    webbrowser.open(PAGE_URL)


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def paper_running(state_dir: Path) -> bool:
    """The paper trader holds an exclusive lock on trader.lock for as long as it runs."""
    lock = single_instance.acquire(state_dir / "trader.lock")
    if lock is None:
        return True
    lock.close()
    return False


def spawn(name: str, cmd: list[str], cwd: Path, env: dict | None = None) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    out = (LOGS / f"{name}.log").open("ab")
    kwargs: dict = {}
    if WINDOWS:
        kwargs["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)
    log(f"started {name} pid={proc.pid}")
    return proc.pid


def run_hidden(name: str, cmd: list[str], cwd: Path, env: dict | None = None, timeout: float = 1800) -> int:
    """Runs a setup step to the end with its output in var/logs/<name>.log. Returns the exit code."""
    LOGS.mkdir(parents=True, exist_ok=True)
    kwargs = {"creationflags": 0x08000000} if WINDOWS else {}
    with (LOGS / f"{name}.log").open("ab") as out:
        try:
            return subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                  timeout=timeout, **kwargs).returncode
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"{name} failed to run: {exc}")
            return 1


def save_pids(pids: dict) -> None:
    PIDS.parent.mkdir(parents=True, exist_ok=True)
    old = load_pids()
    old.update(pids)
    PIDS.write_text(json.dumps(old), encoding="utf-8")


def load_pids() -> dict:
    try:
        return json.loads(PIDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def kill_tree(pid: int) -> None:
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], creationflags=0x08000000,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        else:
            import signal

            os.killpg(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def start() -> int:
    cfg = load_paper()
    state_dir = ROOT / cfg["state_dir"]
    if paper_running(state_dir) and port_open(DATA_PORT) and port_open(PAGE_PORT):
        log("start: already running, only opening the browser")
        open_window()
        return 0

    node = shutil.which("node")
    if not node:
        popup("NEW2", "Node.js가 설치되어 있지 않아요.\nhttps://nodejs.org 에서 LTS 버전을 설치한 뒤 다시 켜 주세요.")
        return 1
    pnpm = shutil.which("pnpm")
    if not pnpm:
        npm = shutil.which("npm")
        if not npm or run_hidden("pnpm-install", [npm, "install", "-g", "pnpm"], ROOT, timeout=600) != 0:
            popup("NEW2", f"pnpm 설치에 실패했어요.\n자세한 내용: {LOGS / 'pnpm-install.log'}")
            return 1
        pnpm = shutil.which("pnpm")
        if not pnpm:
            popup("NEW2", "pnpm을 설치했지만 찾지 못했어요. 컴퓨터를 다시 시작한 뒤 켜 주세요.")
            return 1

    # exchange rules check; never blocks start-up (ADR-0056)
    run_hidden("verify-markets", [sys.executable, str(ROOT / "scripts" / "verify_markets.py"), "--write"], ROOT, timeout=60)

    pids: dict = {}
    if not paper_running(state_dir):
        pids["paper"] = spawn("paper-trader", [sys.executable, str(ROOT / "scripts" / "run_paper_trader.py")], ROOT)
    if not port_open(DATA_PORT):
        pids["data"] = spawn("data-server", [sys.executable, str(ROOT / "scripts" / "run_dashboard.py")], ROOT)
    save_pids(pids)

    if not (NEXT_DIR / "node_modules").exists() or not (NEXT_DIR / ".next").exists():
        popup("NEW2", "처음 한 번은 화면 설치에 몇 분 걸려요. 이 창은 닫아도 돼요.\n끝나면 브라우저가 자동으로 열려요.", wait=False)
    if not (NEXT_DIR / "node_modules").exists() and run_hidden("pnpm-install", [pnpm, "install"], NEXT_DIR) != 0:
        popup("NEW2", f"패키지 설치에 실패했어요.\n자세한 내용: {LOGS / 'pnpm-install.log'}")
        return 1
    env = dict(os.environ, TRADER_API_URL=f"http://127.0.0.1:{DATA_PORT}")
    if not (NEXT_DIR / ".next").exists() and run_hidden("pnpm-build", [pnpm, "build"], NEXT_DIR, env) != 0:
        popup("NEW2", f"화면 빌드에 실패했어요.\n자세한 내용: {LOGS / 'pnpm-build.log'}")
        return 1
    if not port_open(PAGE_PORT):
        save_pids({"page": spawn("page", [pnpm, "start"], NEXT_DIR, env)})

    deadline = time.time() + 120
    while time.time() < deadline and not port_open(PAGE_PORT):
        time.sleep(1)
    if not port_open(PAGE_PORT):
        popup("NEW2", f"화면이 켜지지 않았어요.\n자세한 내용: {LOGS / 'page.log'}")
        return 1
    open_window()
    return 0


def stop() -> int:
    cfg = load_paper()
    state_dir = ROOT / cfg["state_dir"]
    pids = load_pids()
    if paper_running(state_dir):
        (state_dir / "STOP").write_text("stop", encoding="utf-8")  # the trader saves its state and exits (ADR-0057)
        deadline = time.time() + 30
        while time.time() < deadline and paper_running(state_dir):
            time.sleep(1)
        if paper_running(state_dir) and pids.get("paper"):
            log("paper trader did not stop in 30s, ending it")
            kill_tree(pids["paper"])
    for name, port in (("page", PAGE_PORT), ("data", DATA_PORT)):
        if pids.get(name) and port_open(port):
            kill_tree(pids[name])
    try:
        PIDS.unlink()
    except FileNotFoundError:
        pass
    log("stopped")
    return 0


def status() -> int:
    cfg = load_paper()
    state_dir = ROOT / cfg["state_dir"]
    running = paper_running(state_dir)
    line = f"모의투자: {'실행 중' if running else '꺼져 있음'}"
    try:
        saved = datetime.fromisoformat(json.loads((state_dir / "paper_state.json").read_text(encoding="utf-8"))["saved_at"])
        age = (datetime.now(timezone.utc) - saved).total_seconds()
        line += f" (마지막 저장 {int(age)}초 전)"
    except (OSError, ValueError, KeyError):
        line += " (저장된 상태 없음)"
    print(line)
    print(f"자료 서버: {'실행 중' if port_open(DATA_PORT) else '꺼져 있음'}")
    print(f"화면({PAGE_URL}): {'실행 중' if port_open(PAGE_PORT) else '꺼져 있음'}")
    print(f"기록 파일: {LOGS}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in ("start", "stop", "status"):
        print(__doc__)
        return 2
    try:
        return {"start": start, "stop": stop, "status": status}[cmd]()
    except Exception as exc:  # noqa: BLE001  (no console to show a traceback in)
        log(f"{cmd} crashed: {exc!r}")
        popup("NEW2", f"예상하지 못한 오류가 났어요: {exc}\n기록: {LOGS / 'launcher.log'}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
