"""Turn narration display text into text the speech model reads well.

`speakable()` returns the spoken text plus a list of segments mapping spoken ranges back to
display ranges, so word timings from the speech model land on what the agent wrote.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field


@dataclass
class Spoken:
    text: str
    # (spoken_start, spoken_end, display_start, display_end), in order, covering both strings
    segs: list[tuple[int, int, int, int]] = field(default_factory=list)

    def to_display(self, a: int, b: int) -> tuple[int, int]:
        """Display range for the spoken range [a, b)."""
        if not self.segs:
            return a, b
        starts = [s[0] for s in self.segs]
        i = max(0, bisect.bisect_right(starts, a) - 1)
        j = max(0, bisect.bisect_right(starts, max(a, b - 1)) - 1)
        sa, sb, da, db = self.segs[i]
        ta, tb, ea, eb = self.segs[j]
        if sb - sa == db - da:  # copied verbatim: map exactly
            d0 = da + (a - sa)
        else:
            d0 = da
        if tb - ta == eb - ea:
            d1 = ea + (b - ta)
        else:
            d1 = eb
        return d0, max(d0, d1)


class _Builder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.segs: list[tuple[int, int, int, int]] = []
        self.n = 0

    def add(self, spoken: str, d0: int, d1: int) -> None:
        if not spoken and d0 == d1:
            return
        self.parts.append(spoken)
        self.segs.append((self.n, self.n + len(spoken), d0, d1))
        self.n += len(spoken)

    def build(self) -> Spoken:
        return Spoken("".join(self.parts), self.segs)


def split_identifier(word: str) -> str:
    """`useEffect` -> `use effect`, `HF_HUB_OFFLINE` -> `HF hub offline`."""
    word = word.strip("_")
    parts = re.split(r"[_\-]+", word)
    out: list[str] = []
    for p in parts:
        out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p) or [p]
    return " ".join(w if w.isupper() and len(w) <= 4 else w.lower() for w in out if w)


def speak_code(code: str) -> str:
    """Read a short code span aloud."""
    s = code.strip()
    s = re.sub(r"\(\s*\)", "", s)  # foo() -> foo
    s = s.replace(".", " dot ").replace("/", " slash ").replace("=", " equals ")
    words = re.findall(r"[A-Za-z0-9_\-]+|\S", s)
    out = []
    for w in words:
        if re.fullmatch(r"[A-Za-z0-9_\-]+", w):
            out.append(split_identifier(w))
        else:
            out.append(w)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def speakable(
    display: str,
    code: list[tuple[int, int]] | tuple = (),
    say: list[tuple[int, int, str]] | tuple = (),
    lexicon: dict[str, str] | None = None,
) -> Spoken:
    marks: list[tuple[int, int, str]] = []
    for a, b, spoken in say:
        marks.append((a, b, spoken))
    taken = [(a, b) for a, b, _ in marks]
    for a, b in code:
        if not any(a < tb and ta < b for ta, tb in taken):
            marks.append((a, b, speak_code(display[a:b])))
    if lexicon:
        for word, spoken in lexicon.items():
            for m in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", display):
                if not any(m.start() < tb and ta < m.end() for ta, tb, _ in marks):
                    marks.append((m.start(), m.end(), spoken))
    marks.sort()
    b = _Builder()
    pos = 0
    for a, e, spoken in marks:
        if a < pos:
            continue
        b.add(display[pos:a], pos, a)
        b.add(spoken, a, e)
        pos = e
    b.add(display[pos:], pos, len(display))
    return b.build()
