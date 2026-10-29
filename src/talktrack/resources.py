"""What the machine has, and how much of it generating may use.

MP4 export starts a headless Chromium and an x264 encoder per worker, and speech holds a model on
the GPU or in RAM, so sizing them from the CPU count alone froze 8 to 16 GB Macs: 4 workers
at about 0.7 GB each, every x264 on every core, while the daemon voiced narration.

1. **Readers:** small functions that take their raw input (meminfo text, cgroup file text, sysctl
   output) and return a number or None. Nothing here raises; unknown is None.
2. **Snapshot:** `machine()` reads the real OS once into a frozen `Machine`.
3. **Budget:** `budget()` turns a snapshot and `TALKTRACK_MAX_MEMORY` into worker, thread and
   speech-memory limits. Explicit user settings are applied by the callers, not here.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GB = 1024**3
MB = 1024**2
# An estimate, not a measurement: a headless Chromium with the player loaded plus an x264 encoder
# and its frame buffers.
WORKER_BYTES = int(0.7 * GB)
SHARE = 0.5  # of total RAM, by default: the rest is for the OS, the browser the user is in, their editor
HEADROOM = 2 * GB  # always left free for the OS, whatever the ceiling says
# MLX Kokoro peaks near 3 GB (v0.1.8 measurement); below 2 GB it would spend its time evicting.
SPEECH_SHARE = 0.4  # of the ceiling
SPEECH_MIN = 2 * GB
# VoxCPM2 4-bit (the Arabic voices) peaked at 4.66 GB with a 4 GB limit: MLX goes past a limit it
# cannot meet by freeing its cache. While it is loaded, speech may hold up to this much.
ARABIC_SPEECH_MAX = 6 * GB
MAX_WORKERS = 4
LOW_RAM = 8 * GB  # under this, speech uses at most 2 threads
LOW_RAM_THREADS = 2
UNLIMITED = 2**60  # cgroup v1 reports ~2**63 for "no limit"


# -- readers: raw text in, number or None out -----------------------------------------------


def parse_meminfo_available(text: str) -> int | None:
    """Bytes in MemAvailable of /proc/meminfo text."""
    m = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text, re.M)
    return int(m.group(1)) * 1024 if m else None


def parse_cgroup_bytes(text: str) -> int | None:
    """A cgroup memory file (memory.max, memory.current, v1 limit or usage): bytes, or None for
    "max", empty, junk or v1's huge no-limit value."""
    t = text.strip()
    if not t.isdigit():
        return None
    n = int(t)
    return n if 0 < n < UNLIMITED else None


def parse_cpu_max(text: str) -> float | None:
    """cgroup v2 cpu.max ("200000 100000" or "max 100000"): CPUs the quota allows, or None."""
    parts = text.split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    quota, period = int(parts[0]), int(parts[1])
    return quota / period if quota > 0 and period > 0 else None


def parse_sysctl_int(text: str) -> int | None:
    t = text.strip()
    return int(t) if t.isdigit() else None


def macos_available(level_text: str, total: int | None) -> int | None:
    """`sysctl -n kern.memorystatus_level` is the percent of memory free; times the total."""
    level = parse_sysctl_int(level_text)
    if level is None or total is None or level > 100:
        return None
    return total * level // 100


def parse_size(text: str, total: int | None = None) -> int | None:
    """TALKTRACK_MAX_MEMORY: `6GB`, `512MB` or `50%` of total. Invalid, zero or a percent without
    a known total gives None."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(gib|gb|g|mib|mb|m|%)\s*", text, re.I)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    if unit == "%":
        if total is None or not 0 < n <= 100:
            return None
        return int(total * n / 100)
    size = int(n * (GB if unit.startswith("g") else MB))
    return size if size > 0 else None


def lower_memory(total: int | None, available: int | None, limit: int | None, used: int | None):
    """Apply a cgroup memory limit: total drops to the limit when smaller, and available becomes
    the limit minus what the cgroup uses when that is less than the host's figure."""
    if limit is None:
        return total, available
    if total is None or limit < total:
        total = limit
        if used is not None:
            room = max(0, limit - used)
            available = room if available is None else min(available, room)
    return total, available


def lower_cpus(cpus: int, quota: float | None) -> int:
    """At least 1, at most `cpus`, lowered by a cgroup CPU quota."""
    if quota is not None:
        cpus = min(cpus, int(quota))
    return max(1, cpus)


# -- the snapshot ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Machine:
    total: int | None  # bytes of RAM, or None when unknown
    available: int | None  # bytes free to use now, or None when unknown
    cpus: int


def _read(path: str) -> str:
    try:
        return Path(path).read_text()
    except (OSError, ValueError):
        return ""


def _sysctl(name: str) -> str:
    try:
        r = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def machine() -> Machine:
    try:
        total = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):  # Windows
        total = None
    available = None
    if sys.platform == "darwin":
        if total is None:
            total = parse_sysctl_int(_sysctl("hw.memsize"))
        available = macos_available(_sysctl("kern.memorystatus_level"), total)
    elif sys.platform.startswith("linux"):
        available = parse_meminfo_available(_read("/proc/meminfo"))
    limit = used = None
    for lim, cur in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        limit = parse_cgroup_bytes(_read(lim))
        if limit is not None:
            used = parse_cgroup_bytes(_read(cur))
            break
    total, available = lower_memory(total, available, limit, used)
    try:
        cpus = len(os.sched_getaffinity(0))  # respects taskset and container cpusets
    except AttributeError:  # macOS, Windows
        cpus = os.cpu_count() or 1
    return Machine(total, available, lower_cpus(cpus, parse_cpu_max(_read("/sys/fs/cgroup/cpu.max"))))


# -- the budget -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    machine: Machine
    ceiling: int | None  # bytes talktrack may use in all, or None when RAM is unknown

    def room(self) -> int | None:
        """Bytes MP4 export may take now: under the ceiling, and under what is free less headroom."""
        limits = [self.ceiling]
        if self.machine.available is not None:
            limits.append(self.machine.available - HEADROOM)
        known = [x for x in limits if x is not None]
        return max(0, min(known)) if known else None

    def workers_fit(self) -> int | None:
        """How many export workers fit in memory (at least 1), or None when memory is unknown."""
        room = self.room()
        return None if room is None else max(1, room // WORKER_BYTES)

    def export_workers(self) -> int:
        fit = self.workers_fit()
        return max(1, min(MAX_WORKERS, self.machine.cpus, fit if fit is not None else MAX_WORKERS))

    def ffmpeg_threads(self, workers: int) -> int:
        """x264 threads per worker, so all encoders together use about the CPUs there are."""
        return max(1, self.machine.cpus // max(1, workers))

    def speech_limit(self) -> int | None:
        """Bytes the MLX voice may hold, or None when RAM is unknown."""
        return None if self.ceiling is None else max(SPEECH_MIN, int(self.ceiling * SPEECH_SHARE))

    def arabic_speech_limit(self) -> int:
        """Bytes MLX may hold while the Arabic voice is loaded: 6 GB, or less under the ceiling."""
        return ARABIC_SPEECH_MAX if self.ceiling is None else min(ARABIC_SPEECH_MAX, self.ceiling)

    def speech_threads(self, max_threads: int) -> int:
        total = self.machine.total
        cap = LOW_RAM_THREADS if total is not None and total < LOW_RAM else max_threads
        return max(1, min(max_threads, cap, self.machine.cpus))


def budget(m: Machine | None = None, spec: str | None = None) -> Budget:
    """The budget for machine `m` (default: this one). `spec` is TALKTRACK_MAX_MEMORY (default:
    read from the environment); a value that does not parse is ignored."""
    m = m or machine()
    spec = os.environ.get("TALKTRACK_MAX_MEMORY", "") if spec is None else spec
    ceiling = parse_size(spec, m.total) if spec.strip() else None
    if ceiling is None and m.total is not None:
        ceiling = int(m.total * SHARE)
    if ceiling is not None and m.total is not None:
        ceiling = min(ceiling, max(0, m.total - HEADROOM))
    return Budget(m, ceiling)


def describe(b: Budget) -> str:
    """One line for `talktrack doctor`."""
    m = b.machine
    ram = "RAM unknown" if m.total is None else f"{m.total / GB:.0f} GB RAM"
    if m.available is not None:
        ram += f" ({m.available / GB:.0f} GB free)"
    limit = b.speech_limit()
    speech = "unlimited" if limit is None else f"{limit / GB:.0f} GB"
    return (
        f"{ram}, {m.cpus} CPUs; export up to {b.export_workers()} workers, speech limit {speech}"
        f" ({b.arabic_speech_limit() / GB:.0f} GB with the Arabic voice)"
    )
