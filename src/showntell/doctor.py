"""`showntell doctor`: check the install, models, speech speed, port and daemon."""

from __future__ import annotations

import platform
import shutil
import socket
import sys
import time

from . import __version__, daemon
from .config import Config

OK, WARN, FAIL = "ok", "warn", "fail"


def _row(status: str, name: str, detail: str) -> None:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
    print(f"  {mark} {name:<16} {detail}")


def doctor(cfg: Config, args) -> None:
    print(f"showntell {__version__} on Python {platform.python_version()}, {platform.system()} {platform.machine()}")
    problems = 0

    # data folder
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".doctor"
        probe.write_text("ok")
        probe.unlink()
        _row(OK, "data folder", str(cfg.data_dir))
    except OSError as e:
        _row(FAIL, "data folder", f"{cfg.data_dir}: {e}")
        problems += 1

    # git, used for diffs read from the project
    _row(OK if shutil.which("git") else WARN, "git", shutil.which("git") or "not found: diffs from git revisions will not work")

    # speech backend and models
    from .speech import Status, make_backend
    from .speech.mlx_backend import available as mlx_available

    kind = getattr(args, "speech", None) or cfg.speech
    if kind == "auto":
        kind = "mlx" if mlx_available() else "onnx"
    _row(OK, "speech backend", kind + (" (Apple silicon)" if kind == "mlx" else ""))
    status = Status()
    backend = make_backend(kind, cfg.data_dir, status)
    t0 = time.time()
    try:
        backend.load()
        load_s = time.time() - t0
        backend.synth("Warming up.", "af_heart", 1.0)
        t1 = time.time()
        audio, tokens = backend.synth("showntell checks that the voice model works and how fast it speaks.", "af_heart", 1.0)
        compute = time.time() - t1
        dur = len(audio) / 24000
        rtf = dur / compute if compute else 0
        _row(OK, "voice model", f"loaded in {load_s:.1f} s; speaks {rtf:.0f}x faster than real time")
        if not tokens:
            _row(WARN, "word timings", "the backend returned no word timings; the pointer will be approximate")
        else:
            _row(OK, "word timings", f"{len(tokens)} timed words in a {dur:.1f} s sentence")
        if rtf < 2:
            _row(WARN, "speed", "speech is slow on this machine; long walkthroughs may pause while voicing")
    except Exception as e:
        _row(FAIL, "voice model", f"{type(e).__name__}: {e}")
        problems += 1

    # daemon and port
    lock = daemon.running(cfg)
    if lock:
        h = lock["health"]
        same = daemon.same_build(lock)
        _row(OK if same else WARN, "daemon", f"running at {lock['url']} (pid {lock['pid']}, version {lock.get('version')}, "
             f"speech {h['speech']['state']})" + ("" if same else "; it runs other code and will be replaced when idle"))
    else:
        with socket.socket() as s:
            free = s.connect_ex(("127.0.0.1", cfg.port)) != 0
        _row(OK if free else WARN, "port", f"{cfg.port} is free" if free else f"{cfg.port} is in use by another program; showntell will pick the next free port")
        _row(OK, "daemon", "not running; MCP clients start it on demand")

    print()
    print("Connect an MCP client:")
    print("  Claude Code   claude mcp add showntell -- uvx showntell mcp")
    print("  any client    command: uvx showntell mcp   (stdio)")
    print(f"  over HTTP     {cfg.base_url}/mcp   (after `showntell serve`)")
    sys.exit(1 if problems else 0)
