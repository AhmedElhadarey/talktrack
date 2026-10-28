"""`talktrack prune` and the daemon log cap: what goes, what stays."""

from __future__ import annotations

import json
import os
import time

import pytest

from talktrack import daemon
from talktrack.prune import parse_age, prune

DAY = 86400


def _walk(data, wid: str, days_ago: float, keys: list[str]) -> None:
    d = data / "walkthroughs" / wid
    d.mkdir(parents=True)
    state = {
        "id": wid,
        "title": wid.title(),
        "updated": time.time() - days_ago * DAY,
        "slides": [{"id": "s", "says": [{"key": k} for k in keys]}],
    }
    (d / "state.json").write_text(json.dumps(state))


def _audio(data, key: str, days_ago: float) -> None:
    a = data / "audio"
    a.mkdir(exist_ok=True)
    for ext in ("wav", "json"):
        f = a / f"{key}.{ext}"
        f.write_bytes(b"x" * 100)
        t = time.time() - days_ago * DAY
        os.utime(f, (t, t))


@pytest.fixture
def data(tmp_path):
    _walk(tmp_path, "old", 90, ["aaa"])
    _walk(tmp_path, "new", 2, ["bbb", "shared"])
    for key, age in [("aaa", 90), ("bbb", 90), ("shared", 90), ("orphan", 90), ("fresh", 0)]:
        _audio(tmp_path, key, age)
    (tmp_path / "walkthroughs" / "broken").mkdir()
    return tmp_path


def test_old_walkthroughs_and_audio_nothing_uses_go(data):
    r = prune(data, 60 * DAY)
    assert [w["id"] for w in r["walkthroughs"]] == ["old"] and r["audio_files"] == 4
    assert sorted(p.name for p in (data / "walkthroughs").iterdir()) == ["broken", "new"]
    left = sorted({p.name.split(".")[0] for p in (data / "audio").iterdir()})
    assert left == ["bbb", "fresh", "shared"]  # in use, or made a moment ago


def test_dry_run_deletes_nothing(data):
    before = sorted(p.name for p in data.rglob("*"))
    r = prune(data, 60 * DAY, dry_run=True)
    assert [w["id"] for w in r["walkthroughs"]] == ["old"] and r["audio_files"] == 4
    assert sorted(p.name for p in data.rglob("*")) == before


@pytest.mark.parametrize(("text", "seconds"), [("60d", 60 * DAY), ("8w", 56 * DAY), ("12h", 12 * 3600), ("30", 30 * DAY)])
def test_ages(text, seconds):
    assert parse_age(text) == seconds


def test_a_bad_age_is_refused():
    with pytest.raises(ValueError, match="60d"):
        parse_age("two months")


def test_the_log_is_capped_and_the_old_part_kept(tmp_path, monkeypatch):
    log = tmp_path / "daemon.log"
    monkeypatch.setattr(daemon, "LOG_CAP", 1000)
    log.write_bytes(b"a" * 999)
    daemon.cap_log(log)
    assert log.stat().st_size == 999 and not (tmp_path / "daemon.log.1").exists()
    with open(log, "ab") as f:
        f.write(b"b" * 10)
        f.flush()
        daemon.cap_log(log)
        f.write(b"after")
    assert log.read_bytes() == b"after" and (tmp_path / "daemon.log.1").stat().st_size == 1009
