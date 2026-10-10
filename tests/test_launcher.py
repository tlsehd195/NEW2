import importlib.util
import socket
from pathlib import Path

from cointrader.paper import single_instance

SPEC = importlib.util.spec_from_file_location("launcher", Path(__file__).resolve().parents[1] / "scripts" / "launcher.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_port_open_tells_listening_from_closed_ports():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    assert launcher.port_open(port)
    server.close()
    assert not launcher.port_open(port)


def test_paper_running_follows_the_trader_lock(tmp_path):
    assert not launcher.paper_running(tmp_path)  # nobody holds the lock
    held = single_instance.acquire(tmp_path / "trader.lock")
    assert launcher.paper_running(tmp_path)
    held.close()
    assert not launcher.paper_running(tmp_path)


def test_open_window_falls_back_to_default_browser_without_edge_or_chrome(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher, "_app_browser", lambda: None)
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    assert launcher.open_window() is None
    assert opened == [launcher.PAGE_URL]


def test_open_window_uses_an_own_profile_so_closing_the_window_ends_the_process(monkeypatch):
    seen = []

    class Fake:
        pid = 1

    monkeypatch.setattr(launcher, "_app_browser", lambda: "/bin/browser")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd) or Fake())
    assert launcher.open_window() is not None
    assert f"--app={launcher.PAGE_URL}" in seen[0]
    assert any(a.startswith("--user-data-dir=") for a in seen[0])
