"""Turn parsed blocks into slides: visuals, code from disk, diffs, cues and checks.

The builder appends to a walkthrough as blocks arrive. Every problem it finds becomes a
plain-language issue returned to the agent on its next call.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import sources
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
                self.w.questions += [inline_plain(t) for t in b.items if t.strip()]
            elif b.kind == "para":
                self.w.questions.append(inline_plain(b.text))
            return
        if b.kind == "footnote":
            self.w.notes[b.attrs["id"]] = b.text
            return
        if b.kind == "para":
            self._flush_old()
            self._add_say(self._current(), b.text)
            return
        if b.kind == "pause":
            s = self._current()
            say = Say(id=self._say_id(s), pause=min(5.0, b.attrs["seconds"]), status="ready")
            s.says.append(say)
            self.changed.add(s.id)
            return
        if b.kind == "quote":
            s = self._current()
            s.shown = (s.shown + "\n" + b.text).strip() if s.shown else b.text
            self.changed.add(s.id)
            return
        self._add_visual(b)

    # -- visuals ------------------------------------------------------------------------

    def _slide_for_visual(self, kind: str) -> Slide:
        s = self._current()
        if s.visual and not (kind == "list" and s.kind == "list"):
            self._issue("extra_visual", f"Slide `{s.id}` already shows a {s.kind}; the next visual got its own slide.",
                        "Give each visual its own heading.", slide=s.id)
            s = self._new_slide(Block("heading", 0, s.title, attrs={"classes": list(s.classes)}))
        return s

    def _add_visual(self, b: Block) -> None:
        if b.kind == "code":
            flags = b.attrs.get("flags", [])
            if b.lang == "mermaid":
                return self._set(self._slide_for_visual("diagram"), "diagram", self._diagram(b))
            if b.lang in ("math", "latex", "tex"):
                return self._set(self._slide_for_visual("math"), "math", {"latex": b.text.strip()})
            if "old" in flags or "before" in flags:
                ex = self._code_excerpt(b)
                if ex is not None:
                    self.pending_old = (b, ex)
                return
            if ("new" in flags or "after" in flags) and self.pending_old:
                ob, oex = self.pending_old
                self.pending_old = None
                nex = self._code_excerpt(b)
                if nex is None:
                    return
                lang = b.lang or ob.lang
                vis = {
                    "lang": lang,
                    "old": {"code": oex.code, "start": oex.start, "title": ob.attrs.get("title") or self._name(ob)},
                    "new": {"code": nex.code, "start": nex.start, "title": b.attrs.get("title") or self._name(b)},
                }
                self._check_diff(vis)
                return self._set(self._slide_for_visual("diff"), "diff", vis)
            if b.lang == "diff" or (b.attrs.get("rev") and b.attrs.get("file")):
                return self._diff_block(b)
            ex = self._code_excerpt(b)
            if ex is None:
                return
            vis = {"lang": b.lang, "code": ex.code, "start": ex.start, "title": b.attrs.get("title") or self._name(b)}
            if b.attrs.get("file"):
                vis["file"] = b.attrs["file"]
            self._check_code(vis)
            return self._set(self._slide_for_visual("code"), "code", vis)
        if b.kind == "math":
            return self._set(self._slide_for_visual("math"), "math", {"latex": b.text.strip()})
        if b.kind == "table":
            t = parse_table(b.text)
            ids = []
            for i, row in enumerate(t["rows"]):
                m = re.search(r"\{#([\w-]+)\}\s*$", row[-1]) if row else None
                if m:
                    row[-1] = row[-1][: m.start()].rstrip()
                ids.append(m.group(1) if m else f"row-{i + 1}")
            t["ids"] = ids
            return self._set(self._slide_for_visual("table"), "table", t)
        if b.kind == "list":
            s = self._slide_for_visual("list")
            items = [parse_item(t) for t in b.items if t.strip()]
            if s.kind == "list":
                items = s.visual["items"] + items
            used = set()
            for i, it in enumerate(items):
                it["title"], it["body"] = inline_plain(it["title"]), inline_plain(it["body"])
                if not it["id"]:
                    it["id"] = slugify(it["title"] or it["body"])[:24] or f"item-{i + 1}"
                while it["id"] in used:
                    it["id"] += "-2"
                used.add(it["id"])
            style = "cards" if "cards" in s.classes else "steps" if ("steps" in s.classes or b.attrs.get("ordered")) else "bullets"
            return self._set(s, "list", {"style": style, "items": items})
        if b.kind == "image":
            return self._image(b)

    def _set(self, s: Slide, kind: str, visual: dict) -> None:
        s.kind, s.visual = kind, visual
        self.changed.add(s.id)
        for say in s.says:  # cues written before the visual arrived
            self._resolve_cues(s, say, reresolve=True)

    def _name(self, b: Block) -> str:
        f = b.attrs.get("file", "")
        return Path(f).name if f else ""

    def _code_excerpt(self, b: Block) -> sources.Excerpt | None:
        if b.attrs.get("file"):
            try:
                ex, _ = sources.read_excerpt(self.root, b.attrs["file"], b.attrs.get("lines"))
                if b.text.strip():
                    self._issue("code_body_ignored", f"Code block for {b.attrs['file']} had a body; showed the file instead.",
                                "Leave the body empty with file=.", line=b.line)
                if not self.w.sha:
                    self.w.sha = sources.head_sha(self.root)
                return ex
            except sources.SourceError as e:
                self._issue("source_error", f"Line {b.line}: {e}", line=b.line)
                return sources.Excerpt(f"# {e}", 1, 1)
        start = 1
        if b.attrs.get("start", "").isdigit():
            start = int(b.attrs["start"])
        text = b.text
        return sources.Excerpt(text, start, len(text.split("\n")))

    def _diff_block(self, b: Block) -> None:
        if b.attrs.get("file") and b.attrs.get("rev"):
            try:
                d = sources.git_diff(self.root, b.attrs["file"], b.attrs["rev"], b.attrs.get("lines"))
                if not self.w.sha:
                    self.w.sha = sources.head_sha(self.root)
            except sources.SourceError as e:
                self._issue("source_error", f"Line {b.line}: {e}", line=b.line)
                return
            if _generated(b.attrs["file"]):
                self._issue("generated_file", f"Line {b.line}: {b.attrs['file']} is a lockfile or generated file.",
                            "Skip it unless it is the point of the change.", line=b.line)
            lang = b.attrs.get("lang") or _lang_from_name(b.attrs["file"])
            vis = {
                "lang": lang,
                "old": {"code": d.old.code, "start": d.old.start, "title": d.old_title},
                "new": {"code": d.new.code, "start": d.new.start, "title": d.new_title},
                "file": b.attrs["file"],
                "rev": b.attrs["rev"],
            }
        else:
            old, new = sources.split_unified(b.text)
            hunks = sources.hunk_count(b.text)
            if hunks > 1:
                self._issue("diff_hunks", f"Line {b.line}: this diff has {hunks} hunks; line numbers follow the first one.",
                            "Show one hunk per slide, or use file= with rev=.", line=b.line)
            lang = b.attrs.get("lang", "")
            vis = {
                "lang": lang,
                "old": {"code": old.code, "start": old.start, "title": b.attrs.get("title", "")},
                "new": {"code": new.code, "start": new.start, "title": b.attrs.get("title", "")},
            }
        self._check_diff(vis)
        self._set(self._slide_for_visual("diff"), "diff", vis)

    def _flush_old(self) -> None:
        if self.pending_old:
            ob, ex = self.pending_old
            self.pending_old = None
            self._issue("old_without_new", f"Line {ob.line}: an `old` code block had no `new` block after it; showed it as plain code.",
                        line=ob.line)
            vis = {"lang": ob.lang, "code": ex.code, "start": ex.start, "title": ob.attrs.get("title") or self._name(ob)}
            self._set(self._slide_for_visual("code"), "code", vis)

    def _diagram(self, b: Block) -> dict:
        src = b.text.strip("\n")
        first = next((ln.strip() for ln in src.split("\n") if ln.strip() and not ln.strip().startswith("%%")), "")
        kind = first.split()[0].lower() if first else ""
        nodes, labels, order = parse_mermaid(src)
        vis = {"source": src, "type": kind, "nodes": nodes, "labels": labels, "order": order}
        alt = self.w.slides[-1].attrs.get("alt") if self.w.slides else None
        if alt:
            vis["alt"] = alt
        if len(nodes) > self.limits["nodes"]:
            self._issue("diagram_too_big", f"The diagram on line {b.line} has {len(nodes)} nodes; this frame reads well up to {self.limits['nodes']}.",
                        "Split it across slides.", line=b.line)
        return vis

    def _image(self, b: Block) -> None:
        src, alt = b.attrs["src"], b.attrs.get("alt", "")
        s = self._slide_for_visual("image")
        if re.match(r"^https?://", src):
            return self._set(s, "image", {"src": src, "alt": alt})
        try:
            path = sources.resolve(self.root, src)
        except sources.SourceError as e:
            self._issue("image_error", f"Line {b.line}: image {e}", line=b.line)
            return
        if path.suffix.lower() not in IMAGE_TYPES:
            self._issue("image_error", f"Line {b.line}: {path.name} is not an image type the player shows.", line=b.line)
            return
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        name = f"{len(list(self.assets_dir.iterdir())) + 1}-{re.sub(r'[^\w.-]', '_', path.name)}"
        shutil.copyfile(path, self.assets_dir / name)
        if not alt:
            self._issue("image_no_alt", f"Image {path.name} has no alt text.", "Add a short description in the brackets.", line=b.line)
        self._set(s, "image", {"src": f"assets/{name}", "alt": alt})

    # -- checks on visuals --------------------------------------------------------------

    def _check_code(self, vis: dict) -> None:
        lines = vis["code"].split("\n")
        lim = self.limits
        width = max((len(ln.expandtabs(4)) for ln in lines), default=0)
        where = vis.get("title") or "a code block"
        if len(lines) > lim["lines"]:
            self._issue("code_too_tall", f"{where} shows {len(lines)} lines; this frame reads well up to {lim['lines']}.",
                        "Show a narrower range.")
        if width > lim["width"]:
            self._issue("code_too_wide", f"{where} has lines of {width} characters; this frame reads well up to {lim['width']}.",
                        "Show a narrower excerpt or wrap long lines.")

    def _check_diff(self, vis: dict) -> None:
        lim = self.limits
        for side in ("old", "new"):
            lines = vis[side]["code"].split("\n")
            if len(lines) > lim["diff_lines"]:
                self._issue("diff_too_tall", f"The {side} side of a diff shows {len(lines)} lines; diffs read well up to {lim['diff_lines']} per side.",
                            "Show one hunk, or narrow it with lines=.")
                break
        width = max((len(ln.expandtabs(4)) for side in ("old", "new") for ln in vis[side]["code"].split("\n")), default=0)
        if width > lim["diff_width"]:
            self._issue("diff_too_wide", f"A diff has lines of {width} characters; diffs read well up to {lim['diff_width']} per side.",
                        "Use the portrait frame for wide diffs, or show the new code alone.")

    # -- narration ----------------------------------------------------------------------

    def _say_id(self, s: Slide) -> str:
        return f"{s.id}.{len(s.says) + 1}.{s.rev}"

    def _add_say(self, s: Slide, src: str) -> None:
        n = parse_narration(src)
        if not n.display:
            return
        say = Say(id=self._say_id(s), text=n.display, code=n.code, overrides=[list(x) for x in n.say], notes=n.notes)
        say._cues = n.cues  # raw cues, resolved below
        self._resolve_cues(s, say)
        s.says.append(say)
        self.changed.add(s.id)
        self.new_says.append((s, say))

    def _resolve_cues(self, s: Slide, say: Say, reresolve: bool = False) -> None:
        raw: list[Cue] = getattr(say, "_cues", None) or [
            Cue(c["at"], c["end"], c.get("label", ""), c.get("target", ""), c.get("text", ""), c.get("inferred", False))
            for c in say.cues
            if not c.get("inferred")
        ]
        say._cues = raw
        out = []
        for c in raw:
            r = self._resolve(s, c, quiet=not s.visual and not reresolve)
            if r:
                out.append(r)
        if not raw and s.visual:
            out = infer_cues(s, say)
        times = {(c["at"], c.get("target")): c.get("t") for c in say.cues}
        for c in out:
            if (c["at"], c.get("target")) in times and times[(c["at"], c.get("target"))] is not None:
                c["t"] = times[(c["at"], c.get("target"))]
        say.cues = sorted(out, key=lambda c: c["at"])

    def _resolve(self, s: Slide, c: Cue, quiet: bool = False) -> dict | None:
        base = {"at": c.at, "end": c.end, "label": c.label, "target": c.target, "text": c.text}
        t = c.target.strip()
        if not s.visual:
            if t in ("", s.id, "visual"):
                return {**base, "kind": "visual"}
            if not quiet:
                self._issue("cue_no_visual", f"Slide `{s.id}` has no visual for the cue [{c.label}](#{t}).", slide=s.id)
            return {**base, "kind": "visual"}
        if t in ("", s.id, "visual", "slide"):
            if c.text:
                return {**base, "kind": "text"}
            return {**base, "kind": "visual"}
        m = re.fullmatch(r"([+-])?L(\d+)(?:-L?(\d+))?", t, re.I)
        if m and s.kind in ("code", "diff"):
            side = "old" if m.group(1) == "-" else "new"
            a = int(m.group(2))
            b = int(m.group(3)) if m.group(3) else a
            if b < a:
                a, b = b, a
            vis = s.visual if s.kind == "code" else s.visual[side]
            lines = vis["code"].split("\n")
            first, last = vis["start"], vis["start"] + len(lines) - 1
            where = f"slide `{s.id}`" + (f" ({side} side)" if s.kind == "diff" else "")
            if a > last or b < first:
                if c.text:
                    hit = _find_line(lines, c.text, first, a)
                    if hit:
                        self._issue("cue_moved", f"[{c.label}](#{t}): line {a} is outside lines {first}-{last} on {where}; "
                                    f"pointed at line {hit}, where the quoted text is.", slide=s.id)
                        return {**base, "kind": "lines", "side": side, "l0": hit, "l1": hit}
                self._issue("cue_off_code", f"[{c.label}](#{t}) points past the code on {where}, which shows lines {first}-{last}.",
                            "Use the gutter numbers.", slide=s.id)
                return None
            a, b = max(a, first), min(b, last)
            if c.text:
                span = "\n".join(lines[a - first : b - first + 1])
                if _norm(c.text) not in _norm(span):
                    hit = _find_line(lines, c.text, first, a)
                    if hit:
                        self._issue("cue_moved", f"[{c.label}](#{t}): the quoted text is not on line {a}; pointed at line {hit}, where it is.",
                                    "Count lines with the gutter numbers.", slide=s.id)
                        a = b = hit
                    else:
                        self._issue("cue_text_missing", f"[{c.label}](#{t}): \"{c.text}\" does not occur in the code on {where}; pointed at the whole line.",
                                    "Quote the code exactly, spaces included.", slide=s.id)
                        return {**base, "kind": "lines", "side": side, "l0": a, "l1": b, "text": ""}
            return {**base, "kind": "lines", "side": side, "l0": a, "l1": b}
        if m:
            self._issue("cue_wrong_kind", f"[{c.label}](#{t}) points at lines, but slide `{s.id}` shows a {s.kind}, not code.", slide=s.id)
            return {**base, "kind": "visual"}
        if s.kind == "list":
            ids = {it["id"] for it in s.visual["items"]}
            if t in ids:
                return {**base, "kind": "item", "id": t}
            hit = _match_title(t, s.visual["items"])
            if hit:
                return {**base, "kind": "item", "id": hit}
            self._issue("cue_no_target", f"[{c.label}](#{t}): no item `{t}` on slide `{s.id}`. Items: {', '.join(sorted(ids))}.", slide=s.id)
            return None
        if s.kind == "table":
            if t in s.visual.get("ids", []):
                return {**base, "kind": "row", "id": t}
            self._issue("cue_no_target", f"[{c.label}](#{t}): no row `{t}` on slide `{s.id}`.", slide=s.id)
            return None
        if s.kind == "diagram":
            nodes = s.visual.get("nodes", {})
            if t in nodes:
                return {**base, "kind": "node", "id": t, "text": c.text or nodes[t]}
            if nodes and s.visual.get("type") in ("flowchart", "graph"):
                self._issue("cue_no_target", f"[{c.label}](#{t}): no node `{t}` in the diagram on slide `{s.id}`. Nodes: {', '.join(list(nodes)[:12])}.",
                            slide=s.id)
            return {**base, "kind": "node", "id": t, "text": c.text}
        self._issue("cue_wrong_kind", f"[{c.label}](#{t}): slide `{s.id}` shows a {s.kind}.", "Point at it with `#`.", slide=s.id)
        return {**base, "kind": "visual"}

    # -- slide-level checks -------------------------------------------------------------

    def _check_finished_slide(self, s: Slide) -> None:
        if not s.visual and s.kind not in ("title",):
            for x in s.says:
                for c in getattr(x, "_cues", None) or []:
                    if c.target.strip() not in ("", s.id, "visual", "slide"):
                        self._issue("cue_no_visual", f"Slide `{s.id}` has no visual, so the cue [{c.label}](#{c.target}) has nothing to point at.",
                                    slide=s.id, line=s.line)
        if not any(x.text for x in s.says):
            self._issue("no_narration", f"Slide `{s.id}` has no narration.", "Every slide needs at least one spoken paragraph.",
                        slide=s.id, line=s.line)
            return
        if s.kind in ("code", "diff", "diagram", "list", "table") and not any(
            not c.get("inferred") for x in s.says for c in x.cues
        ):
            self._issue("no_cues", f"Slide `{s.id}` has no cues, so the pointer only follows names showntell can match.",
                        "Link the words that name each part, as in [the loader](#L18) or [the cache](#cache).", slide=s.id, line=s.line)
        words = sum(len(x.text.split()) for x in s.says)
        secs = words / WORDS_PER_SECOND
        if s.kind in ("code", "diff"):
            n = len(s.visual.get("code", s.visual.get("new", {}).get("code", "")).split("\n"))
            need = 1.5 + 0.35 * n
            if secs < need * 0.6:
                self._issue("thin_narration", f"Slide `{s.id}` shows {n} lines with about {secs:.0f} s of narration.",
                            "Say more, or show fewer lines.", slide=s.id, line=s.line)
        self._check_spoken(s)
        self._check_build_order(s)
        shown = " ".join([s.shown] + [it.get("body", "") for it in s.visual.get("items", [])])
        spoken = " " + " ".join(re.findall(r"[\w'-]+", " ".join(x.text for x in s.says).lower())) + " "
        for chunk in _phrases(shown, 7):
            if f" {chunk} " in spoken:
                self._issue("reads_screen", f"Slide `{s.id}`: the narration reads the on-screen text aloud (\"{chunk[:40]}...\").",
                            "Say what the screen does not.", slide=s.id, line=s.line)
                break

    def _check_spoken(self, s: Slide) -> None:
        for x in s.says:
            text = x.text
            for a, b in x.code:  # backticked code is spoken as code on purpose
                text = text[:a] + " " * (b - a) + text[b:]
            m = SPOKEN_RE.search(text)
            if m:
                what = "a URL" if m.group("url") else "a handle" if m.group("handle") else "a commit hash"
                self._issue("spoken_handle", f"Slide `{s.id}`: the narration reads {what} aloud (\"{m.group(0)[:40]}\").",
                            "Say the person's name or what the link is, and show the raw text on screen if it matters.",
                            slide=s.id, line=s.line)
                return

    def _check_build_order(self, s: Slide) -> None:
        if "build" not in s.classes or not s.visual:
            return
        paras = [[c for c in x.cues if not c.get("inferred")] for x in s.says if x.text]
        cues = [c for p in paras for c in p]
        if len(paras) >= 2 and len(cues) >= 3 and all(not p for p in paras[1:]):
            self._issue("build_cues_bunched", f"Slide `{s.id}` reveals in steps, but every cue is in its first paragraph, "
                        "so the parts appear at once while the rest of the narration has nothing to show.",
                        "Spread the cues so each part appears as the narration reaches it.", slide=s.id, line=s.line)
            return
        rank = _source_rank(s)
        seq = [rank(c) for c in cues]
        seq = [r for r in seq if r is not None]
        if any(b < a for a, b in zip(seq, seq[1:], strict=False)):
            self._issue("build_cue_order", f"Slide `{s.id}` reveals in steps, but its cues jump back and forth, "
                        "so later parts appear together with earlier ones.",
                        "Write the visual in the order you talk about it, and cue it in that order.", slide=s.id, line=s.line)

    def check_all(self) -> None:
        if self.w.slides:
            self._check_finished_slide(self.w.slides[-1])
        run: list[str] = []
        for s in self.w.slides + [None]:
            if s is not None and s.kind in ("code", "diff"):
                run.append(s.id)
                continue
            if len(run) >= 4:
                self._issue("code_run", f"{len(run)} code slides in a row ({', '.join(run[:5])}) with nothing showing what "
                            "the code does when it runs.", "Add a diagram of the flow or the states it moves through between them.",
                            slide=run[-1])
            run = []
        if self.w.slides:
            first = self.w.slides[0]
            names = [x.text[a:b] for x in first.says for a, b in x.code]
            if names:
                self._issue("opening_code_names", f"The opening names code ({', '.join(f'`{n}`' for n in names[:3])}) "
                            "before the viewer knows what it is for.",
                            "Open with what the change or system does for the viewer; bring the names in on later slides.",
                            slide=first.id, line=first.line)
        openings: dict[str, list[str]] = {}
        for s in self.w.slides:
            first = next((x.text for x in s.says if x.text), "")
            words = re.findall(r"[a-z']+", first.lower())[:2]
            if len(words) == 2:
                openings.setdefault(" ".join(words), []).append(s.id)
        for phrase, ids in openings.items():
            if len(ids) >= 3:
                self._issue("same_openings", f"{len(ids)} slides open with \"{phrase}\" ({', '.join(ids[:4])}).", "Vary the openings.",
                            slide=ids[-1])


# -- helpers ------------------------------------------------------------------------------


SPOKEN_RE = re.compile(
    r"(?P<url>https?://\S+|www\.\S+)|(?P<handle>(?<![\w.])@[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\b(?![.@]\w))"
    r"|(?P<sha>(?<![\w-])(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}(?![\w-]))"
)
GENERATED = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|uv\.lock|poetry\.lock|Cargo\.lock|Gemfile\.lock|go\.sum"
    r"|composer\.lock|Pipfile\.lock|bun\.lockb?)$|\.min\.(js|css)$|(^|/)(dist|build|vendor|node_modules)/|_pb2\.py$|\.generated\."
)


def _generated(name: str) -> bool:
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
    if s.kind == "diagram":
        order = v.get("order", [])
        return lambda c: order.index(c["id"]) if c.get("kind") == "node" and c.get("id") in order else None
    if s.kind in ("code", "terminal"):
        return lambda c: c.get("l0") if c.get("kind") == "lines" else None
    return lambda c: None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _phrases(text: str, n: int) -> list[str]:
    words = re.findall(r"[\w'-]+", text.lower())
    return [" ".join(words[i : i + n]) for i in range(0, max(0, len(words) - n + 1), 3)]


def _find_line(lines: list[str], text: str, first: int, near: int) -> int | None:
    t = _norm(text)
    hits = [first + i for i, ln in enumerate(lines) if t in _norm(ln)]
    if not hits:
        return None
    return min(hits, key=lambda h: abs(h - near))


def _match_title(target: str, items: list[dict]) -> str | None:
    t = target.lower().replace("-", " ").replace("_", " ")
    for it in items:
        if it["title"] and it["title"].lower() == t:
            return it["id"]
    return None


def _lang_from_name(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    return {"py": "python", "js": "javascript", "ts": "typescript", "tsx": "typescript", "jsx": "javascript",
            "rs": "rust", "rb": "ruby", "md": "markdown", "yml": "yaml", "sh": "bash", "zsh": "bash",
            "kt": "kotlin", "cs": "csharp", "h": "c", "hpp": "cpp", "cc": "cpp"}.get(ext, ext)


NODE_RE = re.compile(
    r"(?<![\w-])([A-Za-z_]\w*(?:-\w+)*)\s*(\(\[|\[\[|\[\(|\(\(\(|\(\(|\{\{|\[/|\[\\|\[|\(|\{|>)\s*(\"[^\"]*\"|[^\]\)\}\n]*?)\s*(\]\)|\]\]|\)\]|\)\)\)|\)\)|\}\}|/\]|\\\]|\]|\)|\})"
)
EDGE_LABEL_RE = re.compile(r"\|\s*\"?([^|\"]+?)\"?\s*\||--\s+([^->|][^->]*?)\s+-->")


def parse_mermaid(src: str) -> tuple[dict, list, list]:
    """Node ids with labels (in source order), plus edge and message labels. Best effort."""
    nodes: dict[str, str] = {}
    order: list[str] = []
    labels: list[str] = []
    first = ""
    for raw in src.split("\n"):
        line = raw.split("%%", 1)[0].strip()
        if not line:
            continue
        if not first:
            first = line.split()[0].lower()
            continue
        if first in ("flowchart", "graph"):
            if line.startswith(("subgraph", "end", "classDef", "class ", "style ", "linkStyle", "click ", "direction")):
                m = re.match(r"subgraph\s+([\w-]+)\s*\[(.+?)\]", line)
                if m:
                    labels.append(m.group(2).strip('"'))
                continue
            for m in NODE_RE.finditer(line):
                nid, label = m.group(1), m.group(3).strip().strip('"')
                if nid not in nodes:
                    order.append(nid)
                nodes[nid] = label or nodes.get(nid, nid)
            stripped = NODE_RE.sub(lambda m: m.group(1), line)
            stripped = re.sub(r"\|[^|]*\|", " ", stripped)
            # edge text written inline: a -- calls --> b, a -. maybe .-> b, a == sure ==> b
            stripped = re.sub(r"(--|-\.|==)\s+[^-=.>][^>]*?\s+(-->|\.->|==>|---)", r" \2 ", stripped)
            for nid in re.findall(r"(?<![\w-])([A-Za-z_]\w*(?:-\w+)*)(?![\w])", stripped):
                if nid in ("end", "subgraph", "TD", "TB", "LR", "RL", "BT") or nid in nodes:
                    continue
                if re.search(rf"(?<![\w-]){re.escape(nid)}\s*(-|=|&|$)", stripped) or re.search(
                    rf"(>|-|\||&)\s*{re.escape(nid)}(?![\w-])", stripped
                ):
                    nodes[nid] = nid
                    order.append(nid)
            for m in EDGE_LABEL_RE.finditer(line):
                labels.append((m.group(1) or m.group(2) or "").strip())
        elif first == "sequencediagram":
            m = re.match(r"(?:participant|actor)\s+([\w-]+)(?:\s+as\s+(.+))?$", line)
            if m:
                if m.group(1) not in nodes:
                    order.append(m.group(1))
                nodes[m.group(1)] = (m.group(2) or m.group(1)).strip()
                continue
            m = re.match(r"(\w+(?:-\w+)*)\s*(?:<<)?-{1,2}(?:>>|>|x|\))[+-]?\s*(\w+(?:-\w+)*)\s*:\s*(.+)$", line)
            if m:
                for nid in (m.group(1), m.group(2)):
                    if nid not in nodes:
                        nodes[nid] = nid
                        order.append(nid)
                labels.append(m.group(3).strip())
        elif first.startswith("statediagram"):
            m = re.match(r'state\s+"([^"]+)"\s+as\s+([\w-]+)', line)
            if m:
                nodes[m.group(2)] = m.group(1)
                order.append(m.group(2))
                continue
            m = re.match(r"([\w\[\]*-]+)\s*-->\s*([\w\[\]*-]+)\s*(?::\s*(.+))?$", line)
            if m:
                for nid in (m.group(1), m.group(2)):
                    if nid != "[*]" and nid not in nodes:
                        nodes[nid] = nid
                        order.append(nid)
                if m.group(3):
                    labels.append(m.group(3).strip())
        else:
            for m in re.finditer(r"\"([^\"]{2,})\"|\[([^\]]{2,})\]", line):
                labels.append((m.group(1) or m.group(2)).strip())
    return nodes, labels, order


IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def infer_cues(s: Slide, say: Say) -> list[dict]:
    """Cues for narration that has none: code spans and identifiers that appear in the code,
    list titles, table first cells and diagram labels mentioned by name."""
    text = say.text
    low = text.lower()
    out: list[dict] = []
    seen: set = set()

    def add(at: int, end: int, **kw) -> None:
        key = (kw.get("kind"), kw.get("l0"), kw.get("id"), kw.get("text"))
        if key in seen or len(out) >= 4:
            return
        seen.add(key)
        out.append({"at": at, "end": end, "label": text[at:end], "target": "", "inferred": True, **kw})

    if s.kind in ("code", "diff"):
        sides = [("new", s.visual)] if s.kind == "code" else [("new", s.visual["new"]), ("old", s.visual["old"])]
        for a, b in say.code:
            needle = text[a:b]
            for side, vis in sides:
                lines = vis["code"].split("\n")
                hit = _find_line(lines, needle, vis["start"], vis["start"])
                if hit:
                    add(a, b, kind="lines", side=side, l0=hit, l1=hit, text=needle)
                    break
        if not out:
            idents = set()
            for _, vis in sides:
                idents |= {m.group(0) for m in IDENT_RE.finditer(vis["code"]) if len(m.group(0)) >= 4}
            for m in IDENT_RE.finditer(text):
                w = m.group(0)
                if w in idents and w.lower() not in STOPWORDS and (not w.islower() or "_" in w or len(w) >= 6):
                    for side, vis in sides:
                        hit = _find_line(vis["code"].split("\n"), w, vis["start"], vis["start"])
                        if hit:
                            add(m.start(), m.end(), kind="lines", side=side, l0=hit, l1=hit, text=w)
                            break
    elif s.kind == "list":
        for it in s.visual["items"]:
            title = it["title"] or it["body"]
            if len(title) < 3:
                continue
            i = _find_word(low, title.lower())
            if i is not None:
                add(i, i + len(title), kind="item", id=it["id"])
    elif s.kind == "table":
        for rid, row in zip(s.visual.get("ids", []), s.visual.get("rows", []), strict=False):
            cell = inline_plain(row[0]) if row else ""
            i = _find_word(low, cell.lower()) if len(cell) >= 3 else None
            if i is not None:
                add(i, i + len(cell), kind="row", id=rid)
    elif s.kind == "diagram":
        for nid, label in s.visual.get("nodes", {}).items():
            if len(label) < 3:
                continue
            i = _find_word(low, label.lower())
            if i is not None:
                add(i, i + len(label), kind="node", id=nid, text=label)
    return sorted(out, key=lambda c: c["at"])


def _find_word(hay: str, needle: str) -> int | None:
    m = re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", hay)
    return m.start() if m else None
