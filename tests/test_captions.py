"""WebVTT captions: phrases break at pauses and sentence ends, and each cue covers its words."""

from __future__ import annotations

import re

from showntell.export import MAX_WORDS, phrases, timeline, vtt
from showntell.model import Say, Slide, Walkthrough


def words(text: str, gaps: dict[int, float] | None = None, step: float = 0.3) -> list[dict]:
    """Word timings for text: `step` per word, plus extra silence after word i from `gaps`."""
    out, t = [], 0.05
    for i, m in enumerate(re.finditer(r"\S+", text)):
        w = m.group(0).rstrip(".,;:!?")
        out.append({"s": round(t, 3), "e": round(t + step * 0.85, 3), "c0": m.start(), "c1": m.start() + len(w)})
        t += step + (gaps or {}).get(i, 0.0)
    return out


def say(text: str, gaps=None) -> Say:
    ws = words(text, gaps)
    return Say(id="s.1.1", text=text, status="ready", words=ws, duration=ws[-1]["e"] + 0.1)


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    def secs(ts):
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    blocks = text.strip().split("\n\n")[1:]
    out = []
    for b in blocks:
        _, times, cap = b.split("\n", 2)
        a, z = times.split(" --> ")
        out.append((secs(a), secs(z), cap))
    return out


def test_sentence_ends_and_pauses_break_phrases():
    x = say("The cache is checked first. On a miss we load it and store it for later", gaps={9: 0.6})
    groups = [" ".join(x.text[w["c0"] : w["c1"]] for w in g) for g in phrases(x)]
    assert groups[0] == "The cache is checked first"
    assert groups[1] == "On a miss we load"  # a long silence after "load"
    assert all(len(g) <= MAX_WORDS for g in phrases(x))


def test_a_comma_with_a_pause_breaks_but_a_quick_comma_does_not():
    quick = say("If it fails, try again later today")
    assert len(phrases(quick)) == 1
    slow = say("If it fails, try again later today", gaps={2: 0.25})
    assert len(phrases(slow)) == 2


def test_long_runs_are_split():
    x = say(" ".join(["word"] * 30))
    assert all(len(g) <= MAX_WORDS for g in phrases(x)) and sum(len(g) for g in phrases(x)) == 30


def test_cues_cover_their_words_and_never_overlap():
    w = Walkthrough(id="w" * 22, key="k", report_key="r", title="T")
    w.slides = [
        Slide(id="a", says=[say("First sentence here. Second one follows, after a pause and then more words.", gaps={2: 0.8})]),
        Slide(id="b", says=[say("Another slide speaks now.")]),
    ]
    cues = parse_vtt(vtt(w))
    assert cues
    for (a, z, _), (a2, _, _) in zip(cues, cues[1:], strict=False):
        assert z <= a2 + 1e-6 and a < z
    # every word falls inside the cue that holds it
    for start, _, sy in timeline(w):
        for g in phrases(sy):
            s0, e0 = start + g[0]["s"], start + g[-1]["e"]
            assert any(a <= s0 + 1e-6 and z >= e0 - 1e-6 for a, z, _ in cues), (s0, e0)
    assert all(z - a >= 0.5 for a, z, _ in cues)
