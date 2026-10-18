"""`showntell doctor`: check the install, models, speech speed, port and daemon."""

from __future__ import annotations

import platform
import shutil
import socket
import sys
import time

from . import __version__, daemon
from .config import Config

# Until showntell is on PyPI, where this becomes `uvx showntell mcp`.
INSTALL = "uvx --from git+https://github.com/rahimnathwani/showntell@v0.1.4 showntell mcp"

OK, WARN, FAIL = "ok", "warn", "fail"


def _row(status: str, name: str, detail: str) -> None:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
    print(f"  {mark} {name:<16} {detail}")


def _chromium() -> str:
    """Path of Playwright's Chromium, or '' when Playwright or its browser is missing."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception:
        return ""
    from pathlib import Path

    return path if Path(path).is_file() else ""


def _video_check() -> tuple[str, str, str]:
    """MP4 export needs ffmpeg and Chromium; a missing one is a warning, as only exports need it."""
    missing = [x for x in ("ffmpeg", "ffprobe") if not shutil.which(x)]
    if not _chromium():
        missing.append("Playwright Chromium (the `video` extra, then `playwright install chromium`)")
    if missing:
        return WARN, "video export", "MP4 export and slide checks need " + ", ".join(missing) + "; HTML export works without"
    return OK, "video export", "ffmpeg and Chromium found: MP4 export works"


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

    # MP4 export and the finish-time layout check
    _row(*_video_check())

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
        try:
            backend.load()
            load_s = time.time() - t0
            backend.synth("Warming up.", "af_heart", 1.0)
        except Exception as e:
            if kind != "mlx" or (getattr(args, "speech", None) or cfg.speech) != "auto":
                raise
            # The daemon does the same: it falls back to ONNX when MLX cannot speak.
            _row(WARN, "speech backend", f"MLX failed ({type(e).__name__}: {e}); the daemon speaks with ONNX instead")
            backend = make_backend("onnx", cfg.data_dir, status)
            t0 = time.time()
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
        _row(
            OK if same else WARN,
            "daemon",
            f"running at {lock['url']} (pid {lock['pid']}, version {lock.get('version')}, "
            f"speech {h['speech']['state']})"
            + (
                ""
                if same
                else "; it runs older code and will be replaced when idle"
                if daemon.newer_than(lock)
                else "; it runs newer code than this copy"
            ),
        )
    else:
        with socket.socket() as s:
            free = s.connect_ex(("127.0.0.1", cfg.port)) != 0
        _row(
            OK if free else WARN,
            "port",
            f"{cfg.port} is free" if free else f"{cfg.port} is in use by another program; showntell will pick the next free port",
        )
        _row(OK, "daemon", "not running; MCP clients start it on demand")

    print()
    print("Connect an MCP client:")
    print(f"  Claude Code   claude mcp add --scope user showntell -- {INSTALL}")
    print(f"  any client    command: {INSTALL}   (stdio)")
    print(f"  over HTTP     {cfg.base_url}/mcp   (after `showntell serve`)")
    sys.exit(1 if problems else 0)
