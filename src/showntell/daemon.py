"""The shared background daemon: lockfile, health checks, and starting or replacing it."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .config import Config


class FileLock:
    """An exclusive lock on a file, held until released or the process exits."""

    def __init__(self, path, blocking: bool = True) -> None:
        self.path, self.blocking, self.fh = path, blocking, None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt

                self.fh.seek(0)
                mode = msvcrt.LK_LOCK if self.blocking else msvcrt.LK_NBLCK
                msvcrt.locking(self.fh.fileno(), mode, 1)
            else:
                import fcntl

                fcntl.flock(self.fh, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self) -> None:
        if self.fh:
            self.fh.close()  # closing drops the lock
            self.fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def instance_lock(cfg: Config) -> FileLock | None:
    """Held by the running daemon for its lifetime: one daemon per data folder."""
    lock = FileLock(cfg.data_dir / "daemon.instance.lock", blocking=False)
    return lock if lock.acquire() else None


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
        json.dump({"pid": os.getpid(), "port": cfg.port, "host": cfg.host, "version": _version(), "build": build_id(), "token": token,
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
    if os.name == "nt":
        # os.kill(pid, 0) terminates the process on Windows; ask the OS for a handle instead.
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
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
    """Return the lock of a running daemon of this version, starting or replacing one if needed.
    Several clients starting at once agree on one daemon: the check and the start happen under
    a file lock."""
    with FileLock(cfg.data_dir / "daemon.start.lock"):
        return _ensure(cfg, log)


def _ensure(cfg: Config, log) -> dict:
    lock = running(cfg)
    if lock and not same_build(lock):
        old = f"{lock.get('version')} ({str(lock.get('build') or 'no build id')[:8]})"
        if lock["health"].get("busy"):
            log(f"showntell: a daemon of version {old} is busy; using it for now")
            return lock
        log(f"showntell: replacing daemon version {old} with {_version()} ({build_id()[:8]})")
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
    detach: dict = {"start_new_session": True}
    if os.name == "nt":  # outlive the MCP client's console
        detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
                     env={**os.environ, "PYTHONUNBUFFERED": "1"}, **detach)
    deadline = time.time() + 60
    while time.time() < deadline:
        lock = running(cfg)
        if lock:
            return lock
        time.sleep(0.15)
    raise RuntimeError(f"the showntell daemon did not start; see {cfg.data_dir / 'daemon.log'}")


def same_build(lock: dict) -> bool:
    """Whether a running daemon runs this exact code: same version and same build id. A daemon
    from before build ids counts as different, so it is replaced once it is idle."""
    return lock.get("version") == _version() and lock.get("build") == build_id()


@functools.lru_cache(maxsize=1)
def build_id() -> str:
    """A hash of the package's own files (code, player, text; not vendored libraries), so an
    update installed under the same version number still replaces an idle daemon."""
    root = Path(__file__).parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or "vendor" in p.parts or p.suffix in (".pyc", ".pyo"):
            continue
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _version() -> str:
    from . import __version__

    return __version__
