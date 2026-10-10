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
