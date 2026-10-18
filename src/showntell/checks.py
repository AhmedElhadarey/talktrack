"""Checks on what the agent sent: visual sizes, then each slide once it is finished, then the whole walkthrough."""

from __future__ import annotations

import re

from . import kinds
from .model import Slide

WORDS_PER_SECOND = 2.7
SCROLL_MAX = 400
# Core kinds that should carry cues; every bundled kind should too.
CUED = ("code", "diff", "diagram", "list", "table")

# Text the voice would read out character by character: URLs, @handles and commit hashes.
SPOKEN_RE = re.compile(
    r"(?P<url>https?://\S+|www\.\S+)|(?P<handle>(?<![\w.])@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\b(?![.@]\w))"
    r"|(?P<sha>(?<![\w-])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w-]))"
)
# Visuals made only of words. A few in a row and the walkthrough reads like a document.
PLAIN = ("list", "table", "text")
# Narration that describes a change rather than code as it is.
CHANGE_RE = re.compile(
    r"\b(?:this (?:change|commit|pull request|PR|diff)|the (?:old|previous|new) (?:code|version|behaviou?r)|used to|previously"
    r"|no longer|instead of|before the (?:change|fix|commit)|(?:was|were|got|has been|have been) "
    r"(?:added|removed|renamed|replaced|moved|deleted|changed|dropped|introduced)|now (?:adds|removes|replaces|returns|checks|calls|uses))\b",
    re.I,
)
# A script-style label the voice would read out: "Narration: ...", "Title slide: ...".
LABEL_RE = re.compile(r"^\W*(narration|narrator|voice[- ]?over|speaker notes?|script|transcript|title slide|slide \d+)\W*:", re.I)
# A table cell that is a number, with a sign, currency or unit; bare years do not count.
NUMBER_CELL = re.compile(r"^[~≈<>≤≥+\-−±]?\s*[$€£¥]?\s*\d[\d,_]*(?:\.\d+)?\s*(?:%|×|[A-Za-zµ°/²³]{1,10}(?: [A-Za-z]{1,10})?)?$")
YEAR = re.compile(r"^(1[5-9]|20)\d\d$")
GENERATED = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|uv\.lock|poetry\.lock|Cargo\.lock|Gemfile\.lock|go\.sum"
    r"|composer\.lock|Pipfile\.lock|bun\.lockb?)$|\.min\.(js|css)$|(^|/)(dist|build|vendor|node_modules)/|_pb2\.py$|\.generated\."
)


class Checks:
    """Builder methods that report findings; expect `_issue`, `limits` and `w` from the builder."""

    # -- checks on visuals --------------------------------------------------------------

    def _check_code(self, vis: dict) -> None:
        lines = vis["code"].split("\n")
        lim = self.limits
        width = max((len(ln.expandtabs(4)) for ln in lines), default=0)
        where = vis.get("title") or "a code block"
        if vis.get("scroll"):
            if len(lines) > SCROLL_MAX:
                self._issue(
                    "code_too_tall",
                    f"{where} has {len(lines)} lines; a scrolling block holds up to {SCROLL_MAX}.",
                    "Narrow it with lines=.",
                )
        elif len(lines) > lim["lines"]:
            self._issue(
                "code_too_tall",
                f"{where} shows {len(lines)} lines; this frame reads well up to {lim['lines']}.",
                "Show a narrower range, or add `scroll` to the info string to scroll to each cue.",
            )
        if width > lim["width"]:
            self._issue(
                "code_too_wide",
                f"{where} has lines of {width} characters; this frame reads well up to {lim['width']}.",
                "Show a narrower excerpt or wrap long lines.",
            )

    def _check_diff(self, vis: dict) -> None:
        lim = self.limits
        for side in ("old", "new"):
            lines = vis[side]["code"].split("\n")
            if len(lines) > lim["diff_lines"]:
                self._issue(
                    "diff_too_tall",
                    f"The {side} side of a diff shows {len(lines)} lines; diffs read well up to {lim['diff_lines']} per side.",
                    "Show one hunk, or narrow it with lines=.",
                )
                break
        width = max((len(ln.expandtabs(4)) for side in ("old", "new") for ln in vis[side]["code"].split("\n")), default=0)
        if width > lim["diff_width"]:
            self._issue(
                "diff_too_wide",
                f"A diff has lines of {width} characters; diffs read well up to {lim['diff_width']} per side.",
                "Use the portrait frame for wide diffs, or show the new code alone.",
            )

    # -- slide-level checks -------------------------------------------------------------

    def _check_finished_slide(self, s: Slide) -> None:
        """Checks on a whole slide. It is checked at the end of the call that sent it and again
        when the next heading arrives or at finish; each finding is reported once."""
        start = len(self.issues)
        self._check_slide(s)
        seen = self.__dict__.setdefault("_slide_findings", set())
        fresh = [i for i in self.issues[start:] if (i.code, i.slide, i.message) not in seen]
        seen.update((i.code, i.slide, i.message) for i in fresh)
        self.issues[start:] = fresh

    def _check_slide(self, s: Slide) -> None:
        if not s.visual and s.kind not in ("title",):
            for x in s.says:
                for c in getattr(x, "_cues", None) or []:
                    if c.target.strip() not in ("", s.id, "visual", "slide"):
                        self._issue(
                            "cue_no_visual",
                            f"Slide `{s.id}` has no visual, so the cue [{c.label}](#{c.target}) has nothing to point at.",
                            slide=s.id,
                            line=s.line,
                        )
        if s.kind == "chapter":
            return
        if not any(x.text for x in s.says):
            self._issue(
                "no_narration",
                f"Slide `{s.id}` has no narration.",
                "Every slide needs at least one spoken paragraph.",
                slide=s.id,
                line=s.line,
            )
            return
        if (s.kind in CUED or kinds.get(s.kind)) and not any(not c.get("inferred") for x in s.says for c in x.cues):
            self._issue(
                "no_cues",
                f"Slide `{s.id}` has no cues, so the pointer only follows names showntell can match.",
                "Link the words that name each part, as in [the loader](#L18) or [the cache](#cache).",
                slide=s.id,
                line=s.line,
            )
        words = sum(len(x.text.split()) for x in s.says)
        secs = words / WORDS_PER_SECOND
        if s.kind in ("code", "diff") or kinds.line_kind(s.kind):
            n = len(s.visual.get("code", s.visual.get("new", {}).get("code", "")).split("\n"))
            if s.visual.get("scroll"):
                n = min(n, self.limits["lines"])
            need = 1.5 + 0.35 * n
            if secs < need * 0.6:
                self._issue(
                    "thin_narration",
                    f"Slide `{s.id}` shows {n} lines with about {secs:.0f} s of narration.",
                    "Say more, or show fewer lines.",
                    slide=s.id,
                    line=s.line,
                )
        self._check_spoken(s)
        self._check_build_order(s)
        self._check_fit(s)
        shown = " ".join([s.shown] + [it.get("body", "") for it in s.visual.get("items", [])])
        spoken = " " + " ".join(re.findall(r"[\w'-]+", " ".join(x.text for x in s.says).lower())) + " "
        for chunk in _phrases(shown, 7):
            if f" {chunk} " in spoken:
                self._issue(
                    "reads_screen",
                    f'Slide `{s.id}`: the narration reads the on-screen text aloud ("{chunk[:40]}...").',
                    "Say what the screen does not.",
                    slide=s.id,
                    line=s.line,
                )
                break

    def _check_spoken(self, s: Slide) -> None:
        for x in s.says:
            text = x.text
            for a, b in x.code:  # backticked code is spoken as code on purpose
                text = text[:a] + " " * (b - a) + text[b:]
            m = SPOKEN_RE.search(text)
            if m:
                what = "a URL" if m.group("url") else "a handle" if m.group("handle") else "a commit hash"
                self._issue(
                    "spoken_handle",
                    f'Slide `{s.id}`: the narration reads {what} aloud ("{m.group(0)[:40]}").',
                    "Say the person's name or what the link is, and show the raw text on screen if it matters.",
                    slide=s.id,
                    line=s.line,
                )
                return

    def _check_fit(self, s: Slide) -> None:
        """Whether the visual suits what the slide says: labels the voice would read, a change
        shown as plain code, a table of numbers, stats that do not change."""
        for x in s.says:
            m = LABEL_RE.match(x.text)
            if m:
                self._issue(
                    "narration_label",
                    f'Slide `{s.id}`: a paragraph starts with "{m.group(0).strip()}", which the voice reads aloud.',
                    "Remove the label: every paragraph of prose under a heading is spoken, and nothing else is.",
                    slide=s.id,
                    line=s.line,
                )
                break
        if s.kind == "code" and len(s.visual.get("steps", [])) < 2 and "change_as_code" not in self._once():
            said = " ".join(x.text for x in s.says)
            if len(CHANGE_RE.findall(said)) >= 2:
                self._once().add("change_as_code")
                self._issue(
                    "change_as_code",
                    f"Slide `{s.id}` talks about a change but shows only one version of the code.",
                    "Show the change itself, removed and added lines side by side: `diff file=... rev=...`, "
                    "old/new blocks, or back-to-back `step` versions.",
                    slide=s.id,
                    line=s.line,
                )
        if s.kind == "table":
            cells = [_plain_cell(c) for row in s.visual.get("rows", []) for c in row[1:]]
            nums = [c for c in cells if NUMBER_CELL.match(c) and not YEAR.match(c)]
            if len(s.visual.get("rows", [])) >= 3 and len(nums) >= 3 and len(nums) * 3 >= len(cells) * 2:
                self._issue(
                    "table_numbers",
                    f"Slide `{s.id}`: the table is mostly numbers ({len(nums)} of {len(cells)} cells).",
                    "A chart shows how they compare at a glance (`chart bar` or `hbar`); keep the table only when the exact values matter.",
                    slide=s.id,
                    line=s.line,
                )
        if s.kind == "stat":
            items = s.visual.get("items", [])
            same = [it["label"] for it in items if "before" in it and it["before"] == it["after"]]
            if same:
                self._issue(
                    "stat_no_change",
                    f"Slide `{s.id}`: {', '.join(same[:3])} goes from a value to the same value, so there is no change to show.",
                    "Write `label: value` for a number that does not change, or show a different number.",
                    slide=s.id,
                    line=s.line,
                )
            elif len(items) >= 2 and all(it.get("before") == 0 for it in items):
                self._issue(
                    "stat_no_change",
                    f"Slide `{s.id}`: every number starts from 0, so `before -> after` adds nothing.",
                    "Write `label: value`; a number counts up from zero on its own when cued. Keep `before -> after` for a real change.",
                    slide=s.id,
                    line=s.line,
                )

    def _once(self) -> set:
        """Hints given once per walkthrough."""
        return self.__dict__.setdefault("_hints_given", set())

    def _check_build_order(self, s: Slide) -> None:
        if "build" not in s.classes or not s.visual:
            return
        paras = [[c for c in x.cues if not c.get("inferred")] for x in s.says if x.text]
        cues = [c for p in paras for c in p]
        if len(paras) >= 2 and len(cues) >= 3 and all(not p for p in paras[1:]):
            self._issue(
                "build_cues_bunched",
                f"Slide `{s.id}` reveals in steps, but every cue is in its first paragraph, "
                "so the parts appear at once while the rest of the narration has nothing to show.",
                "Spread the cues so each part appears as the narration reaches it.",
                slide=s.id,
                line=s.line,
            )
            return
        rank = _source_rank(s)
        seq = [rank(c) for c in cues]
        seq = [r for r in seq if r is not None]
        if any(b < a for a, b in zip(seq, seq[1:], strict=False)):
            self._issue(
                "build_cue_order",
                f"Slide `{s.id}` reveals in steps, but its cues jump back and forth, so later parts appear together with earlier ones.",
                "Write the visual in the order you talk about it, and cue it in that order.",
                slide=s.id,
                line=s.line,
            )

    def check_runs(self) -> None:
        """Runs of alike slides: code with nothing showing it run, or words with no picture. Checked
        as slides arrive and at finish; each run is reported once, when it first reaches its length."""
        seen = self.__dict__.setdefault("_runs_seen", set())
        for code, members, need in (("code_run", ("code", "diff"), 4), ("plain_run", PLAIN, 3)):
            run: list[str] = []
            for s in self.w.slides + [None]:
                if s is not None and s.kind in members:
                    run.append(s.id)
                    continue
                if len(run) >= need and (code, run[0]) not in seen:
                    seen.add((code, run[0]))
                    if code == "code_run":
                        self._issue(
                            code,
                            f"{len(run)} code slides in a row ({', '.join(run[:5])}) with nothing showing what the code does when it runs.",
                            "Add a diagram of the flow or the states it moves through between them.",
                            slide=run[-1],
                        )
                    else:
                        self._issue(
                            code,
                            f"{len(run)} slides in a row ({', '.join(run[:5])}) are only words: lists, tables or text.",
                            "Show one as a picture of the idea: a diagram for steps, causes or parts that connect, "
                            "a chart or stat for numbers, a drawing for something physical or spatial.",
                            slide=run[-1],
                        )
                run = []

    def check_all(self) -> None:
        if self.w.slides:
            self._check_finished_slide(self.w.slides[-1])
        self.check_runs()
        if self.w.slides:
            first = self.w.slides[0]
            names = [x.text[a:b] for x in first.says for a, b in x.code]
            if names:
                self._issue(
                    "opening_code_names",
                    f"The opening names code ({', '.join(f'`{n}`' for n in names[:3])}) before the viewer knows what it is for.",
                    "Open with what the change or system does for the viewer; bring the names in on later slides.",
                    slide=first.id,
                    line=first.line,
                )
        openings: dict[str, list[str]] = {}
        for s in self.w.slides:
            first = next((x.text for x in s.says if x.text), "")
            words = re.findall(r"[a-z']+", first.lower())[:2]
            if len(words) == 2:
                openings.setdefault(" ".join(words), []).append(s.id)
        for phrase, ids in openings.items():
            if len(ids) >= 3:
                self._issue(
                    "same_openings", f'{len(ids)} slides open with "{phrase}" ({", ".join(ids[:4])}).', "Vary the openings.", slide=ids[-1]
                )


# -- helpers ------------------------------------------------------------------------------


def generated(name: str) -> bool:
    """Whether a path is a lockfile or generated file, rarely the point of a diff."""
    return bool(GENERATED.search(name))


def _source_rank(s: Slide):
    """Position in the visual of what a cue points at, for checking step-reveal order."""
    v = s.visual
    if s.kind == "list":
        ids = [it["id"] for it in v.get("items", [])]
        return lambda c: ids.index(c["id"]) if c.get("kind") == "item" and c.get("id") in ids else None
    if s.kind == "table":
        ids = v.get("ids", [])
        return lambda c: ids.index(c["id"]) if c.get("kind") == "row" and c.get("id") in ids else None
    if kinds.part_kind(s.kind):
        ids, outer = v.get("ids", []), v.get("part_of", {})
        return lambda c: (
            ids.index(outer.get(c["id"], c["id"])) if c.get("kind") == "item" and outer.get(c.get("id"), c.get("id")) in ids else None
        )
    if s.kind == "diagram":
        order = v.get("order", [])
        return lambda c: order.index(c["id"]) if c.get("kind") == "node" and c.get("id") in order else None
    if s.kind == "code" or kinds.line_kind(s.kind):
        return lambda c: c.get("l0") if c.get("kind") == "lines" else None
    return lambda c: None


def _plain_cell(cell: str) -> str:
    return re.sub(r"[*_`]", "", cell).strip()


def _phrases(text: str, n: int) -> list[str]:
    words = re.findall(r"[\w'-]+", text.lower())
    return [" ".join(words[i : i + n]) for i in range(0, max(0, len(words) - n + 1), 3)]
