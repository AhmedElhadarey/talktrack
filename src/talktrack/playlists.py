"""Playlists: walkthroughs in an order under one title, saved in the data folder.

A playlist holds only walkthrough ids. Titles and durations are read from the walkthroughs when
the page or export is made, so a revised title shows up and a deleted walkthrough drops out.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path

from .export import timeline_total
from .model import Walkthrough
from .store import ID_RE, MAX_DESCRIPTION

MAX_ENTRIES = 50
STATE_VERSION = 1


def playlist_dir(data_dir: Path) -> Path:
    return data_dir / "playlists"


def walkthrough_id(ref: str) -> str | None:
    """The id in a walkthrough's id, handle (`id.key`) or link, or None."""
    ref = (ref or "").strip()
    m = re.search(r"/w/([A-Za-z0-9_-]{16,64})", ref)
    wid = m.group(1) if m else ref.partition(".")[0]
    return wid if ID_RE.match(wid) else None


def _state(data_dir: Path, wid: str) -> dict | None:
    if not ID_RE.match(wid or ""):  # ids read back from a playlist file go into a path
        return None
    path = data_dir / "walkthroughs" / wid / "state.json"
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def create(data_dir: Path, title: str, refs: list[str], description: str = "") -> dict:
    """Save a new playlist. Every call makes a new one, even with the same title."""
    if not refs:
        raise ValueError("a playlist needs at least one walkthrough")
    if len(refs) > MAX_ENTRIES:
        raise ValueError(f"a playlist holds at most {MAX_ENTRIES} walkthroughs; split it into parts")
    ids = [walkthrough_id(r) for r in refs]
    states = [_state(data_dir, wid) if wid else None for wid in ids]
    unknown = [r for r, st in zip(refs, states, strict=True) if st is None]
    if unknown:
        raise ValueError("unknown walkthroughs: " + ", ".join(unknown) + "; use ids or links from list_walkthroughs")
    unfinished = [r for r, st in zip(refs, states, strict=True) if st.get("status") != "finished"]
    if unfinished:
        raise ValueError("unfinished walkthroughs: " + ", ".join(unfinished) + "; call finish_walkthrough on each first")
    pl = {
        "version": STATE_VERSION,
        "id": secrets.token_urlsafe(16),
        "title": (title or "").strip()[:120] or "Untitled playlist",
        "description": " ".join((description or "").split())[:MAX_DESCRIPTION],
        "walkthroughs": ids,
        "created": time.time(),
    }
    d = playlist_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{pl['id']}.json.part"
    tmp.write_text(json.dumps(pl))
    tmp.rename(d / f"{pl['id']}.json")
    return pl


def get(data_dir: Path, pid: str) -> dict | None:
    if not ID_RE.match(pid or ""):
        return None
    try:
        return json.loads((playlist_dir(data_dir) / f"{pid}.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None


def entries(data_dir: Path, pl: dict) -> list[dict]:
    """The playlist's walkthroughs in order, as they are now; deleted ones are skipped."""
    out = []
    for wid in pl["walkthroughs"]:
        st = _state(data_dir, wid)
        if st is None:
            continue
        w = Walkthrough.from_state(st)
        out.append({"id": wid, "title": w.title, "duration": timeline_total(w), "theme": w.theme, "root": w.root, "frame": w.frame})
    return out


def list_all(data_dir: Path) -> list[dict]:
    """Saved playlists, newest first."""
    out = []
    for f in playlist_dir(data_dir).glob("*.json"):
        pl = get(data_dir, f.stem)
        if pl:
            out.append(pl)
    return sorted(out, key=lambda p: p["created"], reverse=True)
