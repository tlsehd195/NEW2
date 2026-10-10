import threading

from cointrader.paper import stop_file


def test_watch_fires_once_when_the_file_appears(tmp_path):
    path = tmp_path / "STOP"
    fired = threading.Event()
    thread = stop_file.watch(path, fired.set, interval=0.01)
    assert not fired.wait(0.1)  # nothing yet
    path.write_text("stop", encoding="utf-8")
    assert fired.wait(2)
    thread.join(2)
    assert not thread.is_alive()


def test_clear_removes_a_stale_file_and_ignores_a_missing_one(tmp_path):
    path = tmp_path / "STOP"
    path.write_text("old", encoding="utf-8")
    stop_file.clear(path)
    stop_file.clear(path)
    assert not path.exists()
