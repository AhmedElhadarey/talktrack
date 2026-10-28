"""The headless browser that draws slides, fetched on first need as the voice model is.

Playwright is a base dependency; its Chromium is not on disk until something launches it. `ensure`
runs Playwright's own installer with this interpreter, once, under a lock: MP4 export starts one
browser per worker at the same time, and they must wait for one download, not start four.
`TALKTRACK_AUTO_BROWSER=0` leaves the download to the user.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path

from .speech.assets import Status

log = logging.getLogger("talktrack.browser")

# What `chromium.launch()` runs in headless mode since Playwright 1.49: the headless shell, not
# the full Chromium. Fetching only it is 99 MiB, against about 250 MiB for `install chromium`.
BROWSER = "chromium-headless-shell"
SIZE = "about 100 MB"
RETRY_AFTER_S = 300  # a failed download is not retried on every finish while offline
INSTALL_TIMEOUT_S = 600  # a stalled download ends as a failure, not a hang (99 MiB took 33 s)

status = Status()  # idle, downloading, ready, error: for /health and doctor
status.set("idle")

_lock = threading.Lock()
_failed: tuple[float, str] | None = None
_job: Future | None = None
_job_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("TALKTRACK_AUTO_BROWSER", "1") != "0"


def describe() -> str:
    if status.state == "downloading":
        return status.describe()
    if status.state == "error":
        return f"download failed: {status.detail}"
    return status.state


def _playwright(*args: str) -> list[str]:
    return [sys.executable, "-m", "playwright", *args]


def installed() -> bool:
    """Whether the headless shell of the pinned Playwright is on disk, found the way its installer
    would: `--dry-run` prints the folder, and a finished download leaves INSTALLATION_COMPLETE."""
    try:
        out = subprocess.run(
            _playwright("install", "--dry-run", BROWSER), capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.search(r"Install location:\s*(.+)", out)
    return bool(m) and (Path(m.group(1).strip()) / "INSTALLATION_COMPLETE").is_file()


def _run_installer() -> tuple[int, str]:
    """Playwright's installer, its progress mirrored to `status` and stderr. Returns the exit code
    and the last lines it printed; it is killed after INSTALL_TIMEOUT_S."""
    proc = subprocess.Popen(
        _playwright("install", BROWSER),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    timed_out = threading.Event()

    def stop() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(INSTALL_TIMEOUT_S, stop)
    timer.start()
    tail: list[str] = []
    shown = -1
    try:
        for line in proc.stdout or ():  # text mode turns the progress bar's \r into line ends
            m = re.search(r"(\d+)% of ([\d.]+) MiB", line)
            if m:
                pct = int(m.group(1))
                status.set("downloading", "Chromium", pct / 100)
                if pct // 25 != shown and float(m.group(2)) > 10:  # a line per quarter of the big file
                    shown = pct // 25
                    print(f"  talktrack: Chromium {pct}% of {m.group(2)} MiB", file=sys.stderr, flush=True)
            elif line.strip():
                tail.append(line.strip())
        code = proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        return code or 1, f"stopped after {INSTALL_TIMEOUT_S} s without finishing"
    return code, "; ".join(tail[-3:])


def manual_steps() -> str:
    from .doctor import BROWSER_HINT

    return BROWSER_HINT


def ensure() -> None:
    """Blocking. Return when the browser is on disk, fetching it if it is not; raises
    `CaptureUnavailable` when auto-fetch is off or the download fails. A browser already on disk
    is used whatever TALKTRACK_AUTO_BROWSER says: the opt-out only stops downloads."""
    global _failed
    from .capture import CaptureUnavailable

    with _lock:
        if installed():
            if status.state != "ready":
                status.set("ready")
            return
        if not enabled():
            raise CaptureUnavailable(
                "Playwright's Chromium is not installed and TALKTRACK_AUTO_BROWSER=0 stops talktrack fetching it", manual_steps()
            )
        if _failed and time.monotonic() - _failed[0] < RETRY_AFTER_S:
            raise CaptureUnavailable(f"Playwright's Chromium could not be downloaded ({_failed[1]})", manual_steps())
        print(f"talktrack: fetching Chromium for slide checks, pictures and video ({SIZE}, once)", file=sys.stderr, flush=True)
        log.info("Downloading Playwright's %s", BROWSER)
        status.set("downloading", "Chromium", 0.0)
        t0 = time.monotonic()
        try:
            code, why = _run_installer()
        except OSError as e:
            code, why = 1, str(e)
        if code != 0:
            why = why or f"installer exited with {code}"
            _failed = (time.monotonic(), why)
            status.set("error", why)
            print(f"talktrack: could not fetch Chromium: {why}", file=sys.stderr, flush=True)
            raise CaptureUnavailable(f"Playwright's Chromium could not be downloaded ({why})", manual_steps())
        _failed = None
        status.set("ready")
        print(f"talktrack: Chromium ready in {time.monotonic() - t0:.0f} s", file=sys.stderr, flush=True)


def begin() -> Future:
    """`ensure` on a background thread, one at a time: the daemon starts the download and answers
    the agent while it runs (its shim gives up on a call after 70 s). The future holds the
    `CaptureUnavailable`, if any."""
    global _job
    with _job_lock:
        if _job is None or _job.done():
            job = _job = Future()

            def run() -> None:
                try:
                    ensure()
                    job.set_result(None)
                except BaseException as e:
                    job.set_exception(e)

            threading.Thread(target=run, daemon=True, name="talktrack-browser").start()
        return _job
