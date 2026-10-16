"""The shared daemon: lockfile, liveness, starting it with ensure(), and shutting it down over RPC."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
import urllib.request

import pytest

from showntell import __version__, daemon
from showntell.config import Config
from showntell.service import RemoteService


@pytest.fixture
def cfg(tmp_path, free_port) -> Config:
    return Config(host="127.0.0.1", port=free_port, data_dir=tmp_path / "data", public_url="", access_token="")


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# -- lockfile ---------------------------------------------------------------------------------------


def test_write_and_read_lock(cfg):
    daemon.write_lock(cfg, "tok")
    lock = daemon.read_lock(cfg)
    assert lock["pid"] == os.getpid() and lock["port"] == cfg.port and lock["token"] == "tok"
    assert lock["version"] == __version__ and lock["url"] == f"http://localhost:{cfg.port}"


def test_lock_is_private(cfg):
    daemon.write_lock(cfg, "tok")
    assert stat.S_IMODE(cfg.lockfile.stat().st_mode) == 0o600
    assert not cfg.lockfile.with_suffix(".part").exists()


def test_clear_lock_removes_our_own_lock(cfg):
    daemon.write_lock(cfg, "tok")
    daemon.clear_lock(cfg)
    assert not cfg.lockfile.exists() and daemon.read_lock(cfg) is None


def test_clear_lock_leaves_another_process_lock(cfg):
    cfg.data_dir.mkdir(parents=True)
    cfg.lockfile.write_text(json.dumps({"pid": os.getpid() + 1, "port": cfg.port}))
    daemon.clear_lock(cfg)
    assert cfg.lockfile.exists()


def test_read_lock_tolerates_missing_and_corrupt_files(cfg):
    assert daemon.read_lock(cfg) is None
    cfg.data_dir.mkdir(parents=True)
    cfg.lockfile.write_text("{not json")
    assert daemon.read_lock(cfg) is None


# -- liveness ------------------------------------------------------------------------------------------


def test_running_is_none_without_a_lock(cfg):
    assert daemon.running(cfg) is None


def test_running_is_none_for_a_dead_pid(cfg):
    cfg.data_dir.mkdir(parents=True)
    cfg.lockfile.write_text(json.dumps({"pid": dead_pid(), "port": cfg.port, "token": "t"}))
    assert daemon.running(cfg) is None


def test_running_is_none_when_nothing_answers_health(cfg):
    daemon.write_lock(cfg, "tok")  # our own pid is alive, but nothing listens on the port
    assert daemon.running(cfg) is None


def test_health_is_none_when_nothing_listens(free_port):
    assert daemon.health(free_port, timeout=0.2) is None


def test_ensure_passes_the_data_folder_to_the_child(cfg, monkeypatch):
    monkeypatch.delenv("SHOWNTELL_DATA", raising=False)
    seen = {}

    class FakePopen:
        def __init__(self, args, env=None, **kw):
            seen["args"], seen["env"] = args, env or {}

    calls = iter([None, {"pid": 1, "port": cfg.port, "token": "t", "version": __version__}])
    monkeypatch.setattr(daemon, "running", lambda c: next(calls))
    monkeypatch.setattr(daemon.subprocess, "Popen", FakePopen)
    daemon.ensure(cfg, log=lambda m: None)
    args = seen["args"]
    given = args[args.index("--data") + 1] if "--data" in args else seen["env"].get("SHOWNTELL_DATA")
    assert given == str(cfg.data_dir)


def test_ensure_reuses_a_running_daemon_of_this_version(cfg, monkeypatch):
    lock = {"pid": os.getpid(), "port": cfg.port, "token": "t", "version": __version__, "health": {"busy": False}}
    monkeypatch.setattr(daemon, "running", lambda c: lock)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: pytest.fail("must not start another daemon"))
    assert daemon.ensure(cfg, log=lambda m: None) is lock


def test_ensure_keeps_a_busy_daemon_of_another_version(cfg, monkeypatch):
    lock = {"pid": os.getpid(), "port": cfg.port, "token": "t", "version": "0.0.1", "health": {"busy": True}}
    monkeypatch.setattr(daemon, "running", lambda c: lock)
    monkeypatch.setattr(daemon, "_shutdown", lambda lock: pytest.fail("must not stop a busy daemon"))
    messages = []
    assert daemon.ensure(cfg, log=messages.append) is lock
    assert "busy" in messages[0]


# -- the real thing -------------------------------------------------------------------------------------


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as r:
        return json.loads(r.read())


def _post(url: str, token: str) -> dict:
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read())


def _reap(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if done:
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def fake_daemon_env(cfg, monkeypatch):
    """Environment for a child daemon: fake speech, data in tmp, no browser, no token."""
    monkeypatch.setenv("SHOWNTELL_SPEECH", "fake")
    monkeypatch.setenv("SHOWNTELL_DATA", str(cfg.data_dir))
    monkeypatch.delenv("SHOWNTELL_OPEN", raising=False)
    monkeypatch.delenv("SHOWNTELL_ACCESS_TOKEN", raising=False)
    started: list[int] = []
    yield started
    for pid in started:  # never leave a daemon behind
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        _reap(pid, 1)


def _shut_down(cfg: Config, lock: dict) -> None:
    assert _post(f"http://127.0.0.1:{cfg.port}/api/rpc/shutdown", lock["token"]) == {"result": "bye"}
    assert _reap(int(lock["pid"]), 10), "the daemon did not exit"


@pytest.mark.slow
async def test_ensure_starts_a_daemon_that_serves_and_shuts_down(cfg, fake_daemon_env):
    lock = daemon.ensure(cfg, log=lambda m: None)
    pid = int(lock["pid"])
    fake_daemon_env.append(pid)
    assert lock["port"] == cfg.port and lock["version"] == __version__ and pid != os.getpid()
    assert stat.S_IMODE(cfg.lockfile.stat().st_mode) == 0o600
    health = _get_json(f"http://127.0.0.1:{cfg.port}/health")
    assert health["app"] == "showntell" and health["speech"]["backend"] == "fake"
    assert daemon.ensure(cfg, log=lambda m: None)["pid"] == pid  # a second ensure reuses it

    svc = RemoteService(f"http://127.0.0.1:{cfg.port}", lock["token"])
    try:
        start = await svc.start("Daemon test", "landscape", "", 1.0, None)
        await svc.add(start["handle"], "# Daemon test\n\nThe daemon voices this sentence.\n")
        assert (await svc.playable(start["handle"], 10))["status"] == "ready"
    finally:
        await svc.client.aclose()
    assert (cfg.data_dir / "walkthroughs" / start["handle"].split(".")[0] / "state.json").is_file()

    _shut_down(cfg, lock)
    assert daemon.health(cfg.port, 0.2) is None and daemon.running(cfg) is None


@pytest.mark.slow
def test_shutdown_clears_the_lockfile(cfg, fake_daemon_env):
    lock = daemon.ensure(cfg, log=lambda m: None)
    fake_daemon_env.append(int(lock["pid"]))
    _shut_down(cfg, lock)
    assert not cfg.lockfile.exists()


@pytest.mark.slow
def test_concurrent_clients_share_one_daemon(tmp_path):
    """Several MCP clients starting at once must not each start a daemon."""
    import concurrent.futures
    import os
    import socket
    import urllib.request

    from showntell import daemon
    from showntell.config import Config

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    old = os.environ.get("SHOWNTELL_SPEECH")
    os.environ["SHOWNTELL_SPEECH"] = "fake"
    try:
        def start(_):
            cfg = Config()
            cfg.data_dir, cfg.port = tmp_path, port
            return daemon.ensure(cfg, log=lambda m: None)["pid"]

        with concurrent.futures.ThreadPoolExecutor(4) as ex:
            pids = set(ex.map(start, range(4)))
        assert len(pids) == 1
        lock = json.loads((tmp_path / "daemon.json").read_text())
        req = urllib.request.Request(f"http://127.0.0.1:{lock['port']}/api/rpc/shutdown", data=b"{}", method="POST",
                                     headers={"Authorization": f"Bearer {lock['token']}", "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    finally:
        if old is None:
            os.environ.pop("SHOWNTELL_SPEECH", None)
        else:
            os.environ["SHOWNTELL_SPEECH"] = old
