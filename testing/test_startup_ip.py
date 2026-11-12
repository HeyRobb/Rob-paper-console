import subprocess

import pytest

import app.main as main_module


class _Result:
    def __init__(self, stdout):
        self.stdout = stdout


def _fake_run(outputs):
    calls = {"n": 0}

    def run(*args, **kwargs):
        index = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return _Result(outputs[index])

    return run, calls


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(main_module.time, "monotonic", lambda: clock["now"])

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(main_module.time, "sleep", sleep)


def test_returns_the_first_ipv4_address(monkeypatch):
    run, _ = _fake_run(["10.0.0.128 fe80::1 \n"])
    monkeypatch.setattr(subprocess, "run", run)

    assert main_module.get_startup_ip_address() == "10.0.0.128"


def test_skips_loopback_link_local_and_ipv6(monkeypatch):
    run, _ = _fake_run(["127.0.0.1 169.254.3.4 2001:db8::1 192.168.1.50\n"])
    monkeypatch.setattr(subprocess, "run", run)

    assert main_module.get_startup_ip_address() == "192.168.1.50"


def test_waits_for_the_network_to_come_up(monkeypatch):
    run, calls = _fake_run(["\n", "\n", "10.0.0.7\n"])
    monkeypatch.setattr(subprocess, "run", run)

    assert main_module.get_startup_ip_address(timeout_seconds=10) == "10.0.0.7"
    assert calls["n"] == 3


def test_gives_up_after_the_timeout(monkeypatch):
    run, calls = _fake_run(["\n"])
    monkeypatch.setattr(subprocess, "run", run)

    assert main_module.get_startup_ip_address(timeout_seconds=3) is None
    assert calls["n"] >= 2


def test_command_failure_counts_as_no_address(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("hostname")

    monkeypatch.setattr(subprocess, "run", boom)

    assert main_module.get_startup_ip_address(timeout_seconds=2) is None
