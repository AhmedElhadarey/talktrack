"""The shared background daemon: lockfile, health checks, and starting or replacing it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

from .config import Config


def read_lock(cfg: Config) -> dict | None:
    try:
        return json.loads(cfg.lockfile.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def write_lock(cfg: Config, token: str) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.lockfile.with_suffix(".part")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"pid": os.getpid(), "port": cfg.port, "host": cfg.host, "version": _version(), "token": token,
                   "url": cfg.base_url, "started": time.time()}, f)
    os.replace(tmp, cfg.lockfile)


def clear_lock(cfg: Config) -> None:
    lock = read_lock(cfg)
    if lock and lock.get("pid") == os.getpid():
        try:
            cfg.lockfile.unlink()
        except OSError:
            pass


def health(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            data = json.loads(r.read())
            return data if data.get("app") == "showntell" else None
    except Exception:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def running(cfg: Config) -> dict | None:
    """The lockfile of a live daemon, with its health, or None."""
    lock = read_lock(cfg)
    if not lock or not _alive(int(lock.get("pid", 0))):
        return None
    h = health(int(lock["port"]))
    if not h:
        return None
    return {**lock, "health": h}


def _shutdown(lock: dict) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{lock['port']}/api/rpc/shutdown", data=b"{}", method="POST",
                                 headers={"Authorization": f"Bearer {lock['token']}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def ensure(cfg: Config, log=print) -> dict:
    """Return the lock of a running daemon of this version, starting or replacing one if needed."""
    lock = running(cfg)
    if lock and lock.get("version") != _version():
        if lock["health"].get("busy"):
            log(f"showntell: a daemon of version {lock.get('version')} is busy; using it for now")
            return lock
        log(f"showntell: replacing daemon version {lock.get('version')} with {_version()}")
        _shutdown(lock)
        for _ in range(50):
            if not _alive(int(lock["pid"])):
                break
            time.sleep(0.1)
        lock = None
    if lock:
        return lock
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logfile = open(cfg.data_dir / "daemon.log", "ab")
    args = [sys.executable, "-m", "showntell", "serve", "--port", str(cfg.port), "--host", cfg.host, "--data", str(cfg.data_dir)]
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile, start_new_session=True,
                     env={**os.environ, "PYTHONUNBUFFERED": "1"})
    deadline = time.time() + 30
    while time.time() < deadline:
        lock = running(cfg)
        if lock:
            return lock
        time.sleep(0.15)
    raise RuntimeError(f"the showntell daemon did not start; see {cfg.data_dir / 'daemon.log'}")


def _version() -> str:
    from . import __version__

    return __version__
