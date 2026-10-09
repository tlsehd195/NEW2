from cointrader.paper import single_instance


def test_second_holder_is_refused_until_the_first_lets_go(tmp_path):
    path = tmp_path / "paper" / "trader.lock"
    first = single_instance.acquire(path)
    assert first is not None
    assert single_instance.acquire(path) is None  # another process (here: another handle) must not run
    first.close()
    again = single_instance.acquire(path)
    assert again is not None
    again.close()


def test_a_different_process_is_refused_too(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / "trader.lock"
    held = single_instance.acquire(path)
    assert held is not None
    code = ("import sys; from pathlib import Path; from cointrader.paper import single_instance as s;"
            "sys.exit(0 if s.acquire(Path(sys.argv[1])) is None else 1)")
    src = str(Path(__file__).resolve().parents[1] / "src")
    r = subprocess.run([sys.executable, "-c", code, str(path)], env={"PYTHONPATH": src}, capture_output=True)
    held.close()
    assert r.returncode == 0, r.stderr
