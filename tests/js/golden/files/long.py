"""Turn parsed blocks into slides: visuals, code from disk, diffs, cues and checks.

The builder appends to a walkthrough as blocks arrive. Every problem it finds becomes a
plain-language issue returned to the agent on its next call.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import kinds, sources
from .format import Block, Cue, Parser, inline_plain, parse_item, parse_narration, parse_table
from .issues import Issue
from .model import Say, Slide, Walkthrough

# Comfortable sizes, measured on the player at each frame (scripts/measure_limits.py).
LIMITS = {
    "landscape": {"lines": 18, "width": 90, "diff_lines": 18, "diff_width": 46, "nodes": 12},
    "square": {"lines": 26, "width": 68, "diff_lines": 22, "diff_width": 32, "nodes": 10},
    "portrait": {"lines": 34, "width": 48, "diff_lines": 18, "diff_width": 52, "nodes": 8},
}
WORDS_PER_SECOND = 2.7
CHART_MAX = 12
STAT_MAX = 4
CHAT_MAX = 8
SCROLL_MAX = 400
IMAGE_FRAMES = ("browser", "window", "phone")
SPEED_RANGE = (0.8, 1.3)
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
STOPWORDS = set(
    "the a an and or but if then else for from with into onto over under this that these those it its "
    "is are was were be been being to of in on at by as so not no yes we you they he she our your their "
    "here there when where what which who how why all any each every some more most less one two three "
    "first next last also just only very can will would should could may might must do does did done "
    "has have had get gets got set sets new old value values line lines code call calls return returns".split()
)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40].strip("-") or "slide"


class Builder:
    def __init__(self, w: Walkthrough, assets_dir: Path) -> None:
        self.w = w
        self.assets_dir = assets_dir
        self.parser = Parser()
        self.questions_mode = False
        self.pending_old: tuple[Block, sources.Excerpt] | None = None
        self.issues: list[Issue] = []
        self.new_says: list[tuple[Slide, Say]] = []
        self.changed: set[str] = set()

    @property
    def root(self) -> Path | None:
        return Path(self.w.root) if self.w.root else None

    @property
    def limits(self) -> dict:
        return LIMITS.get(self.w.frame, LIMITS["landscape"])

    def _issue(self, code: str, message: str, fix: str = "", *, slide: str | None = None, line: int | None = None) -> None:
        if slide is None and self.w.slides:
            slide = self.w.slides[-1].id
        self.issues.append(Issue(code, message, fix, slide=slide, line=line or None))

    # -- appending ----------------------------------------------------------------------

    def add(self, markdown: str) -> None:
        for block in self.parser.feed(markdown):
            self._apply(block)
        if self.parser.pending.strip():
            first = self.parser.pending.strip().split("\n", 1)[0][:40]
            self._issue("block_open", f"`{first}` is still open at the end of this call; your next add_slides call completes it.",
                        "Close every code block within one call.")

    def _current(self) -> Slide:
        if not self.w.slides:
            self._new_slide(Block("heading", 0, self.w.title, attrs={"classes": []}), implicit=True)
        return self.w.slides[-1]

    def _new_slide(self, b: Block, implicit: bool = False) -> Slide:
        if self.w.slides and not implicit:
            self._check_finished_slide(self.w.slides[-1])
        attrs = dict(b.attrs)
        classes = attrs.pop("classes", [])
        sid = attrs.pop("id", "") or slugify(b.text)
        sid = self._unique_id(sid)
        s = Slide(id=sid, title=inline_plain(b.text), classes=classes, attrs=attrs, line=b.line)
        s.kind = "title" if not self.w.slides else "text"
        if "chapter" in classes and self.w.slides:
            s.kind = "chapter"
            s.visual = {"n": 1 + sum(1 for x in self.w.slides if x.kind == "chapter")}
        self.w.slides.append(s)
        self.changed.add(s.id)
        return s

    def _unique_id(self, sid: str) -> str:
        taken = {s.id for s in self.w.slides}
        if sid not in taken:
            return sid
        n = 2
        while f"{sid}-{n}" in taken:
            n += 1
        return f"{sid}-{n}"

    def _apply(self, b: Block) -> None:
        if b.kind == "front":
            from .model import FRAMES
            from .speech.base import VOICES

            a = b.attrs
            if a.get("title"):
                self.w.title = a["title"][:120]
            if a.get("frame"):
                if a["frame"] in FRAMES:
                    self.w.frame = a["frame"]
                else:
                    self._issue("front_matter", f"Front matter frame `{a['frame']}` is not one of {', '.join(FRAMES)}; kept {self.w.frame}.", line=b.line)
            if a.get("voice"):
                if a["voice"] in VOICES:
                    self.w.voice = a["voice"]
                else:
                    self._issue("front_matter", f"Front matter voice `{a['voice']}` is unknown; kept {self.w.voice}.", line=b.line)
            if a.get("theme"):
                from .themes import THEMES

                if a["theme"] in THEMES:
                    self.w.theme = a["theme"]
                else:
                    self._issue("front_matter", f"Front matter theme `{a['theme']}` is not one of {', '.join(THEMES)}; kept {self.w.theme}.", line=b.line)
            if a.get("speed"):
                try:
                    self.w.speed = max(SPEED_RANGE[0], min(SPEED_RANGE[1], float(a["speed"])))
                except ValueError:
                    self._issue("front_matter", f"Front matter speed `{a['speed']}` is not a number; kept {self.w.speed}.", line=b.line)
            return
        if b.kind == "heading":
            self._flush_old()
            if "questions" in b.attrs.get("classes", []):
                self.questions_mode = True
                return
            self.questions_mode = False
            self._new_slide(b)
            return
        if self.questions_mode:
            if b.kind == "list":
