"""resources.py: readers fed fake machine files, and the worker counts they give."""

from __future__ import annotations

import pytest

from talktrack import resources as r
from talktrack.resources import GB, Machine, budget

MEMINFO = "MemTotal:       16384000 kB\nMemFree:         1000000 kB\nMemAvailable:    6291456 kB\n"


def test_meminfo_and_sysctl():
    assert r.parse_meminfo_available(MEMINFO) == 6291456 * 1024
    assert r.parse_meminfo_available("MemTotal: 1 kB") is None
    assert r.macos_available("36\n", 16 * GB) == 16 * GB * 36 // 100
    assert r.macos_available("", 16 * GB) is None
    assert r.macos_available("36", None) is None
    assert r.macos_available("250", 16 * GB) is None


def test_cgroup_limits():
    assert r.parse_cgroup_bytes("4294967296\n") == 4 * GB
    assert r.parse_cgroup_bytes("max\n") is None
    assert r.parse_cgroup_bytes("9223372036854771712") is None  # v1 "no limit"
    assert r.parse_cgroup_bytes("") is None
    assert r.lower_memory(16 * GB, 10 * GB, 4 * GB, 1 * GB) == (4 * GB, 3 * GB)
    assert r.lower_memory(16 * GB, 10 * GB, 32 * GB, 1 * GB) == (16 * GB, 10 * GB)  # larger limit: ignored
    assert r.lower_memory(16 * GB, None, None, None) == (16 * GB, None)
    assert r.lower_memory(None, None, 4 * GB, None) == (4 * GB, None)


def test_cpu_quota():
    assert r.parse_cpu_max("200000 100000\n") == 2.0
    assert r.parse_cpu_max("max 100000") is None
    assert r.parse_cpu_max("junk") is None
    assert r.lower_cpus(10, 2.0) == 2
    assert r.lower_cpus(10, 0.5) == 1
    assert r.lower_cpus(4, 8.0) == 4
    assert r.lower_cpus(4, None) == 4


@pytest.mark.parametrize(
    "text,total,want",
    [
        ("6GB", None, 6 * GB),
        ("6 gb", None, 6 * GB),
        ("512MB", None, 512 * 1024**2),
        ("1.5G", None, int(1.5 * GB)),
        ("50%", 16 * GB, 8 * GB),
        ("50%", None, None),
        ("150%", 16 * GB, None),
        ("0GB", None, None),
        ("lots", 16 * GB, None),
        ("", 16 * GB, None),
        ("6", 16 * GB, None),
    ],
)
def test_parse_size(text, total, want):
    assert r.parse_size(text, total) == want


def test_budget_env_and_invalid_falls_back():
    m = Machine(16 * GB, 12 * GB, 10)
    assert budget(m, "").ceiling == 8 * GB  # default: half the RAM
    assert budget(m, "6GB").ceiling == 6 * GB
    assert budget(m, "25%").ceiling == 4 * GB
    assert budget(m, "nonsense").ceiling == 8 * GB
    assert budget(m, "64GB").ceiling == 14 * GB  # never past total less headroom
    assert budget(Machine(None, None, 4), "").ceiling is None
    assert budget(Machine(None, None, 4), "3GB").ceiling == 3 * GB


def test_budget_reads_env(monkeypatch):
    monkeypatch.setenv("TALKTRACK_MAX_MEMORY", "4GB")
    assert budget(Machine(16 * GB, 12 * GB, 4)).ceiling == 4 * GB


@pytest.mark.parametrize(
    "total,free,cpus,want",
    [
        (8, 2, 4, 1),  # 2 GB free less 2 GB headroom leaves nothing: still one
        (8, 4, 4, 2),
        (8, 6, 10, 4),  # ceiling 4 GB bounds it: 5 would fit, and so would 4
        (16, 6, 10, 4),
        (16, 6, 2, 2),
        (16, 3, 10, 1),
        (32, 20, 10, 4),
        (32, 20, 4, 4),
        (32, 20, 1, 1),
    ],
)
def test_export_workers(total, free, cpus, want):
    assert budget(Machine(total * GB, free * GB, cpus), "").export_workers() == want


def test_unknown_memory_keeps_cpu_rule():
    b = budget(Machine(None, None, 10), "")
    assert b.workers_fit() is None
    assert b.export_workers() == 4
    assert budget(Machine(None, None, 2), "").export_workers() == 2
    assert b.speech_limit() is None


def test_unknown_available_uses_ceiling_only():
    assert budget(Machine(16 * GB, None, 10), "").export_workers() == 4
    assert budget(Machine(8 * GB, None, 10), "1GB").workers_fit() == 1


def test_threads_per_worker():
    b = budget(Machine(32 * GB, 20 * GB, 10), "")
    assert b.ffmpeg_threads(4) == 2
    assert b.ffmpeg_threads(1) == 10
    assert b.ffmpeg_threads(16) == 1


def test_speech_limit_and_threads():
    assert budget(Machine(16 * GB, 6 * GB, 10), "").speech_limit() == int(8 * GB * 0.4)
    assert budget(Machine(4 * GB, 2 * GB, 4), "").speech_limit() == 2 * GB  # floor
    assert budget(Machine(16 * GB, 6 * GB, 10), "").speech_threads(6) == 6
    assert budget(Machine(8 * GB, 6 * GB, 10), "").speech_threads(6) == 6  # exactly 8 GB is not low
    assert budget(Machine(4 * GB, 2 * GB, 10), "").speech_threads(6) == 2
    assert budget(Machine(16 * GB, 6 * GB, 3), "").speech_threads(6) == 3


def test_arabic_speech_limit():
    assert budget(Machine(16 * GB, 6 * GB, 10), "").arabic_speech_limit() == 6 * GB  # 8 GB ceiling
    assert budget(Machine(8 * GB, 6 * GB, 10), "").arabic_speech_limit() == 4 * GB  # under the ceiling
    assert budget(Machine(32 * GB, 6 * GB, 10), "").arabic_speech_limit() == 6 * GB  # never more
    assert budget(Machine(16 * GB, 6 * GB, 10), "5GB").arabic_speech_limit() == 5 * GB
    assert budget(Machine(None, None, 2), "").arabic_speech_limit() == 6 * GB


def test_describe():
    assert r.describe(budget(Machine(16 * GB, 6 * GB, 10), "")) == (
        "16 GB RAM (6 GB free), 10 CPUs; export up to 4 workers, speech limit 3 GB (6 GB with the Arabic voice)"
    )
    assert "RAM unknown" in r.describe(budget(Machine(None, None, 2), ""))


def test_machine_never_raises_and_is_sane(monkeypatch):
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no sysctl")))
    m = r.machine()
    assert m.cpus >= 1
    assert m.available is None or m.available >= 0


def test_warn_workers(capsys):
    from talktrack.video import _segment_cmd, warn_workers

    b = budget(Machine(8 * GB, 3 * GB, 4), "")
    warn_workers(1, b, False)
    warn_workers(4, b, True)
    assert capsys.readouterr().err == ""
    warn_workers(4, b, False)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "--workers 4" in err
    cmd = _segment_cmd(30, "x.mp4", "jpeg", 3)
    assert cmd[cmd.index("-threads") + 1] == "3"
