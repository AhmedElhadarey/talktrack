"""Turn narration display text into text the speech model reads well.

`speakable()` returns the spoken text plus a list of segments mapping spoken ranges back to
display ranges, so word timings from the speech model land on what the agent wrote.

What gets rewritten, in priority order:

1. `[text]{say="..."}` overrides, as written (only words the voice would skip are filled in);
2. the user's word list (`lexicon`);
3. code spans: identifiers split into words, symbols named, files, paths and flags read out;
4. plain prose: only what the voice gets wrong. Units after numbers, versions, complexity,
   links, paths, flags, and acronyms and product names from data/words.txt;
5. any word misaki (Kokoro's English phonemizer) has no pronunciation for.

misaki runs here without its espeak-ng fallback (espeak-ng is GPL), so it skips words missing
from its dictionary in silence. Such a word gets a pronunciation from data/words.txt, or one
built from dictionary pieces (`lockfile` = lock + file), suffixes (`formatter`) or letter-to-sound
rules (`zustand`), or is spelled out when it has no vowel to read (`fzf`).

Pronunciations that known words cannot spell are passed to misaki inline as `[word](/phonemes/)`.
misaki keeps `word` as the token text, so timings still find it in the spoken text.
"""

from __future__ import annotations

import bisect
import functools
import importlib.resources
import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

WORDS_FILE = Path(__file__).parent / "data" / "words.txt"


@dataclass
class Spoken:
    text: str
    # (spoken_start, spoken_end, display_start, display_end), in order, covering both strings
    segs: list[tuple[int, int, int, int]] = field(default_factory=list)
    # parallel to segs: True where the display text was copied as is. When missing, a segment
    # whose spoken and display lengths match counts as copied.
    copied: list[bool] = field(default_factory=list)

    def is_copied(self, k: int) -> bool:
        if k < len(self.copied):
            return self.copied[k]
        sa, sb, da, db = self.segs[k]
        return sb - sa == db - da

    def to_display(self, a: int, b: int) -> tuple[int, int]:
        """Display range for the spoken range [a, b). Inside copied text the map is exact; a
        word inside a rewrite maps to everything the rewrite stands for."""
        if not self.segs:
            return a, b
        starts = [s[0] for s in self.segs]
        i = max(0, bisect.bisect_right(starts, a) - 1)
        j = max(0, bisect.bisect_right(starts, max(a, b - 1)) - 1)
        sa, sb, da, db = self.segs[i]
        ta, tb, ea, eb = self.segs[j]
        d0 = da + (a - sa) if self.is_copied(i) else da
        d1 = ea + (b - ta) if self.is_copied(j) else eb
        return d0, max(d0, d1)


class _Builder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.segs: list[tuple[int, int, int, int]] = []
        self.copied: list[bool] = []
        self.n = 0

    def add(self, spoken: str, d0: int, d1: int, copied: bool = False) -> None:
        if not spoken and d0 == d1:
            return
        self.parts.append(spoken)
        self.segs.append((self.n, self.n + len(spoken), d0, d1))
        self.copied.append(copied)
        self.n += len(spoken)

    def build(self) -> Spoken:
        return Spoken("".join(self.parts), self.segs, self.copied)


# -- misaki's dictionary ----------------------------------------------------------------------

_lock = threading.Lock()
_dicts: tuple[dict, dict] | None = None


def warm() -> None:
    """Load misaki's dictionary and the word list now (about 60 ms) rather than on first use."""
    _dictionary()
    _word_list()


def _dictionary() -> tuple[dict, dict]:
    """misaki's US gold and silver dictionaries, word -> phonemes (or {tag: phonemes})."""
    global _dicts
    if _dicts is None:
        with _lock:
            if _dicts is None:
                try:
                    data = importlib.resources.files("misaki.data")
                    gold = json.loads(data.joinpath("us_gold.json").read_text(encoding="utf-8"))
                    silver = json.loads(data.joinpath("us_silver.json").read_text(encoding="utf-8"))
                except Exception:  # no misaki: nothing can be checked, so leave every word to the voice
                    gold, silver = {}, {}
                _dicts = (gold, silver)
    return _dicts


def _lookup(word: str, silver: bool = True) -> str | None:
    """misaki's phonemes for a dictionary word, trying the case variants misaki tries."""
    dicts = _dictionary()[: 2 if silver else 1]
    for w in (word, word.lower(), word.capitalize()):
        for d in dicts:
            v = d.get(w)
            if isinstance(v, dict):
                v = v.get("DEFAULT")
            if v:
                return v
    return None


def _stems(w: str):
    """Stems misaki tries for an inflected word: -s, -es, -ies, -'s, -ed, -d, -ing."""
    if w.endswith("'s"):
        yield w[:-2]
    if len(w) > 2 and w.endswith("s") and not w.endswith("ss"):
        yield w[:-1]
    if len(w) > 4 and w.endswith("es") and not w.endswith("ies"):
        yield w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        yield w[:-3] + "y"
    if len(w) > 3 and w.endswith("d") and not w.endswith("dd"):
        yield w[:-1]
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        yield w[:-2]
    if len(w) > 4 and w.endswith("ing"):
        yield w[:-3]
        yield w[:-3] + "e"
        if len(w) > 5 and re.search(r"([bcdgklmnprstvz])\1ing$|cking$", w):
            yield w[:-4]


def known(word: str) -> bool:
    """Would misaki pronounce this word? `word` is one piece: letters in one case pattern."""
    gold, silver = _dictionary()
    if len(word) < 2 or not word.isascii() or not (gold or silver):
        return True
    if _lookup(word) or word.isupper():  # misaki spells unknown capitals letter by letter
        return True
    return any(_lookup(s) for s in _stems(word.lower()))


# -- phonemes -----------------------------------------------------------------------------------

_VOWELS = frozenset("AIOQWYaiuæɑɒɔəɛɜɪʊʌᵻ")  # misaki's vowel symbols
_PHONEMES = frozenset("AIOWYbdfhijklmnpstuvwzæðŋɑɔəɛɜɡɪɹɾʃʊʌʒʤʧˈˌθᵊᵻʔT ")  # misaki's US set, plus T and space


def _stress(ps: str, main: bool) -> str:
    """Secondary stress throughout, then primary on the first stressed vowel when `main`."""
    ps = ps.replace("ˈ", "ˌ")
    if not main:
        return ps
    if "ˌ" in ps:
        return ps.replace("ˌ", "ˈ", 1)
    i = next((k for k, c in enumerate(ps) if c in _VOWELS), None)
    return ps if i is None else ps[:i] + "ˈ" + ps[i:]


def _plural(ps: str) -> str:
    """misaki's -s: /s/ after voiceless sounds, /ᵻz/ after sibilants, /z/ otherwise."""
    last = ps.rstrip("ˈˌ ")[-1:]
    if last in ("p", "t", "k", "f", "θ"):
        return ps + "s"
    if last in ("s", "z", "ʃ", "ʒ", "ʧ", "ʤ"):
        return ps + "ᵻz"
    return ps + "z"


def _past(ps: str) -> str:
    """misaki's -ed."""
    last = ps.rstrip("ˈˌ ")[-1:]
    if last in ("p", "k", "f", "θ", "ʃ", "s", "ʧ"):
        return ps + "t"
    if last in ("t", "d"):
        return ps + "ᵻd"
    return ps + "d"


# misaki's US phonemes in its British notation, longest first: no r after vowels, goat is Q,
# lot is ɒ, trap is a, long vowels marked, no flap T.
_TO_BRITISH = [
    ("ɑɹ", "ɑː"),
    ("ɔɹ", "ɔː"),
    ("ɜɹ", "ɜː"),
    ("əɹ", "ə"),
    ("ɛɹ", "ɛː"),
    ("ɪɹ", "ɪə"),
    ("ʊɹ", "ʊə"),
    ("O", "Q"),
    ("ɑ", "ɒ"),
    ("ɔ", "ɔː"),
    ("æ", "a"),
    ("ˈi", "ˈiː"),
    ("ˌi", "ˌiː"),
    ("u", "uː"),
    ("T", "t"),
    ("ɾ", "t"),
    ("ʔ", "t"),
    ("ᵻ", "ɪ"),
]
_INLINE_RE = re.compile(r"\[([^\]]+)\]\(/([^)]*)/\)")


def to_british(ps: str) -> str:
    """US phonemes as misaki writes British ones, for British voices."""
    for us, gb in _TO_BRITISH:
        ps = ps.replace(us, gb)
    return ps.replace("ɒː", "ɑː").replace("ːː", "ː")


def _inline(word: str, ps: str) -> str:
    """misaki's inline pronunciation: says `ps`, keeps `word` as the token text."""
    label = re.sub(r"[^\w.'-]", "", word) or "x"
    return f"[{label}](/{ps}/)"


def _letters(s: str) -> str:
    """Phonemes for spelling `s` out, stressed like misaki stresses acronyms (last letter)."""
    ps = [_stress(_lookup(c.upper()) or "", False) for c in s if c.isalnum()]
    if not ps:
        return ""
    ps[-1] = _stress(ps[-1], True)
    return "".join(ps)


def spell(s: str) -> str:
    """Spoken text that spells `s` letter by letter."""
    if len(s) == 1:
        # "a" and "A" can be read as the article; every other letter reads as its name
        return _inline(s, _letters(s)) if s in "aA" else s.upper()
    up = s.upper()
    low = up.lower()
    if up.isalpha() and up.isascii() and not _lookup(low) and not any(_lookup(x) for x in _stems(low)):
        return up  # misaki spells capitals it cannot read as a word (AWS reads as "awes")
    return _inline(s, _letters(s))


# -- the word list ------------------------------------------------------------------------------


@functools.cache
def _word_list() -> tuple[dict[str, str], dict[str, str]]:
    """data/words.txt as ({lowercased word: spoken}, {exact-case word: spoken})."""
    loose: dict[str, str] = {}
    exact: dict[str, str] = {}
    target = loose
    for line in WORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[exact case]":
            target = exact
            continue
        word, sep, spoken = line.partition("=")
        if sep and word.strip() and spoken.strip():
            word = word.strip()
            target[word if target is exact else word.lower()] = spoken.strip()
    return loose, exact


def _listed_value(word: str) -> str | None:
    loose, exact = _word_list()
    return exact.get(word) or loose.get(word.lower())


def _render(value: str, word: str, plural: bool = False, depth: int = 0) -> str:
    """Spoken text for a word-list value (`/phonemes/` or a respelling), said for `word`."""
    if value.startswith("/") and value.endswith("/"):
        ps = value.strip("/")
        return _inline(word, _plural(ps) if plural else ps)
    out: list[str] = []
    for w in re.findall(r"(?:\b[A-Z]\b ?)+|\S+", value):  # runs of single capitals are spelled together
        w = w.strip()
        if re.fullmatch(r"[A-Z]( [A-Z])*", w):
            out.append(spell(w.replace(" ", "")))
        elif depth < 1 and w.isalpha() and not w.isupper() and not known(w):
            out.append(say_word(w, depth + 1) or w)  # a respelling may use other words from the list
        else:
            out.append(w)
    return _plural_reading(" ".join(out)) if plural else " ".join(out)


def _listed(word: str, depth: int = 0) -> str | None:
    """Spoken text from the word list for `word`, its plural or possessive, or None."""
    value = _listed_value(word)
    if value is not None:
        return _render(value, word, depth=depth)
    low = word.lower()
    for suffix in ("'s", "’s", "es", "s"):
        stem = word[: -len(suffix)]
        if low.endswith(suffix) and len(stem) > 1 and (value := _listed_value(stem)) is not None:
            return _render(value, word, plural=True, depth=depth)
    return None


def _listed_phonemes(word: str) -> str | None:
    """Phonemes for a word-list entry, for building other words from it."""
    value = _listed_value(word)
    if value is None:
        return None
    if value.startswith("/"):
        return value.strip("/")
    ps = []
    for w in value.split():
        if w.isupper() and w.isalpha():
            p = _letters(w)
        elif w.isalpha():
            p = _lookup(w) or (None if w == word else _listed_phonemes(w))
        else:
            return None
        if not p:
            return None
        ps.append(p)
    return "".join(ps)


# -- reading one word ---------------------------------------------------------------------------

_PIECE_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+|[^\W\d_]+")


def _pieces(word: str) -> list[str]:
    """`getUserById` -> get User By Id, `HTTPServer` -> HTTP Server, `int64` -> int 64."""
    return _PIECE_RE.findall(word)


@functools.lru_cache(maxsize=8192)
def say_word(word: str, depth: int = 0) -> str | None:
    """Spoken text for one word (letters and digits, any case mix), or None if misaki reads it right."""
    if (hit := _listed(word, depth)) is not None:
        return hit
    if re.fullmatch(r"\d+(?:st|nd|rd|th|s|'s)", word):
        return None  # ordinals and decades: misaki reads these
    m = re.fullmatch(r"(.+?)(?:['’]s|s['’])", word)
    if m:  # possessive: the word, then -s
        base = say_word(m.group(1), depth)
        return None if base is None else _plural_reading(base)
    pieces = _pieces(word)
    if len(pieces) <= 1:
        return _say_piece(word, depth)
    said = [_say_piece(p, depth) for p in pieces]
    # misaki splits camelCase itself, but not an acronym run into a word (HTTPServer)
    if all(s is None for s in said) and not re.search(r"[A-Z]{2,}[a-z]", word):
        return None
    return " ".join(s or p for s, p in zip(said, pieces, strict=True))


def _say_piece(p: str, depth: int = 0) -> str | None:
    if not p or p.isdigit():
        return None
    if (hit := _listed(p, depth)) is not None:
        return hit
    if p.isupper():
        # an acronym misaki spells, or a word misaki knows; a long unknown one may be words (README)
        if len(p) <= 4 or _lookup(p):
            return None
        ps = _guess(p.lower(), allow_rules=False)
        return _inline(p, ps) if ps else None
    if p in ("a", "A"):
        return None  # the article, in prose
    if known(p):
        return None
    if not p.isascii() or not p.isalpha():
        return None
    if not re.search(r"[aeiouy]", p.lower()):
        return spell(p)  # nothing to sound out: fzf, rm
    return _inline(p, _guess(p.lower()))


# -- guessing a pronunciation -----------------------------------------------------------------

# Affixes that are not words themselves; their phonemes join a dictionary stem.
_PREFIXES = {"un": "ˌʌn", "re": "ɹˌi", "de": "dˌi", "pre": "pɹˌi", "non": "nˌɑn", "sub": "sˌʌb", "dis": "dˌɪs", "mis": "mˌɪs", "co": "kˌO"}
_SUFFIXES = {
    "s": "<s>",
    "es": "<s>",
    "ed": "<ed>",
    "ing": "ɪŋ",
    "ings": "ɪŋz",
    "er": "əɹ",
    "ers": "əɹz",
    "able": "əbᵊl",
    "ible": "əbᵊl",
    "ability": "əbˈɪlᵻTi",
    "ize": "ˌIz",
    "izes": "ˌIzᵻz",
    "ized": "ˌIzd",
    "izing": "ˌIzɪŋ",
    "izer": "ˌIzəɹ",
    "izers": "ˌIzəɹz",
    "ization": "əzˈAʃən",
    "ify": "əfˌI",
    "ified": "əfˌId",
    "less": "ləs",
    "ness": "nəs",
    "ful": "fəl",
    "ment": "mənt",
    "ments": "mənts",
    "ly": "li",
    "y": "i",
    "ish": "ɪʃ",
    "ism": "ɪzəm",
    "ist": "ɪst",
    "ists": "ɪsts",
}
# Two-letter words that may stand as pieces of a compound
_SHORT = frozenset("up in on go do to me my by no so we it at as or of if an be he us ok".split())


def _options(w: str, j: int, i: int):
    """Ways to read w[j:i] inside a longer word: (cost, phonemes, role)."""
    s, n = w[j:i], len(w)
    if j == 0 and i < n and s in _PREFIXES:
        yield 0.8, _PREFIXES[s], "prefix"
    if j > 0 and i == n:
        if s in _SUFFIXES:
            yield 0.6, _SUFFIXES[s], "suffix"
        # a doubled consonant before a suffix: format-t-er, plug-g-able
        if len(s) > 2 and s[0] == w[j - 1] and s[0] not in "aeiouy" and s[1:] in _SUFFIXES and s[1] in "aeiouy":
            yield 0.6, _SUFFIXES[s[1:]], "suffix"
    if len(s) >= 3 or s in _SHORT:
        ps = _lookup(s, silver=len(s) > 3)
        if ps:
            yield (1.0 if len(s) > 2 else 1.5), ps, "word"
        elif i < n and w[i] in "aeiouy" and (ps := _lookup(s + "e", silver=len(s) > 3)):
            yield 1.2, ps, "word"  # serializ-able, compos-able
    if len(s) >= 3 and (ps := _listed_phonemes(s)):
        yield 1.0, ps, "word"
    if len(s) == 1:
        yield 1.3, _letters(s), "letter"
    elif (ps := _lts(s)) is not None:
        yield 1.8 + 0.12 * len(s), ps, "rules"  # sounding out costs more the longer it runs


@functools.lru_cache(maxsize=4096)
def _guess(w: str, allow_rules: bool = True) -> str:
    """Phonemes for a lowercase word no dictionary has: the cheapest split into dictionary
    words, word-list entries, affixes, letter-to-sound chunks and spelled letters."""
    # -ied, -ier, -iest on a dictionary word ending in y (copied), which misaki does not try
    m = re.fullmatch(r"(\w+)i(ed|er|ers|est)", w)
    if m and (ps := _lookup(m.group(1) + "y")):
        return {"ed": _past(ps), "er": ps + "əɹ", "ers": ps + "əɹz", "est": ps + "ɪst"}[m.group(2)]
    n = len(w)
    best: list[tuple[float, int, str, str] | None] = [None] * (n + 1)
    best[0] = (0.0, 0, "", "")
    for i in range(1, n + 1):
        for j in range(max(0, i - 24), i):
            if best[j] is None:
                continue
            for cost, ps, role in _options(w, j, i):
                if not allow_rules and role == "rules":
                    continue
                c = best[j][0] + cost
                if best[i] is None or c < best[i][0]:
                    best[i] = (c, j, ps, role)
    if best[n] is None:
        return _letters(w) if allow_rules else ""
    pieces: list[tuple[str, str]] = []
    i = n
    while i > 0:
        _, j, ps, role = best[i]
        pieces.append((ps, role))
        i = j
    pieces.reverse()
    if not allow_rules and len(pieces) > 3:
        return ""
    # main stress: a suffix that carries it (-ization), else the first word, else the last letter
    roles = [r for _, r in pieces]
    if all(r == "letter" for r in roles):
        main = len(pieces) - 1
    else:
        main = next((k for k, (ps, r) in enumerate(pieces) if r == "suffix" and "ˈ" in ps), None)
        if main is None:
            main = next((k for k, r in enumerate(roles) if r not in ("prefix", "suffix")), 0)
    out = ""
    for k, (ps, _) in enumerate(pieces):
        if ps == "<s>":
            out = _plural(out)
        elif ps == "<ed>":
            out = _past(out)
        else:
            out += _stress(ps, k == main)
    return out


# Letter-to-sound rules, longest spelling first. Enough to make an unknown name audible.
_LTS_GROUPS = sorted(
    {
        "tion": "ʃən",
        "sion": "ʒən",
        "ture": "ʧəɹ",
        "ough": "O",
        "augh": "ɔ",
        "eigh": "A",
        "igh": "I",
        "tch": "ʧ",
        "dge": "ʤ",
        "sch": "sk",
        "chr": "kɹ",
        "que": "k",
        "ch": "ʧ",
        "sh": "ʃ",
        "th": "θ",
        "ph": "f",
        "ck": "k",
        "ng": "ŋ",
        "qu": "kw",
        "qw": "kw",
        "wh": "w",
        "gh": "ɡ",
        "ee": "i",
        "ea": "i",
        "oo": "u",
        "ou": "W",
        "ow": "O",
        "oa": "O",
        "ai": "A",
        "ay": "A",
        "ei": "A",
        "ey": "A",
        "oi": "Y",
        "oy": "Y",
        "au": "ɔ",
        "aw": "ɔ",
        "ue": "u",
        "ew": "ju",
        "ie": "i",
        "ar": "ɑɹ",
        "er": "əɹ",
        "ir": "ɜɹ",
        "ur": "ɜɹ",
        "or": "ɔɹ",
    }.items(),
    key=lambda kv: -len(kv[0]),
)
_LTS_ONE = {
    "a": "æ",
    "b": "b",
    "c": "k",
    "d": "d",
    "e": "ɛ",
    "f": "f",
    "g": "ɡ",
    "h": "h",
    "i": "ɪ",
    "j": "ʤ",
    "k": "k",
    "l": "l",
    "m": "m",
    "n": "n",
    "o": "ɑ",
    "p": "p",
    "q": "k",
    "r": "ɹ",
    "s": "s",
    "t": "t",
    "u": "ʌ",
    "v": "v",
    "w": "w",
    "x": "ks",
    "y": "ɪ",
    "z": "z",
}
_LTS_LONG = {"a": "A", "e": "i", "i": "I", "o": "O", "u": "u", "y": "I"}
_LTS_FINAL = {"a": "ə", "e": "i", "i": "i", "o": "O", "u": "u", "y": "i"}
_ONSETS = frozenset(
    "bl br ch cl cr dr dw fl fr gl gn gr kl kn kr kw ph pl pr ps qu qw sc sh sk sl sm sn sp st sv sw th tr "
    "tw wh wr zh sch scr shr sph spl spr squ str thr".split()
)


def _lts(s: str) -> str | None:
    """Phonemes for `s` from spelling rules, or None when it does not look sayable as a word."""
    vowels = "aeiouy"
    if not any(c in vowels for c in s) or not s.isalpha() or not s.isascii():
        return None
    onset = re.match(r"[^aeiouy]*", s).group(0)
    if len(onset) > 1 and onset not in _ONSETS:
        return None
    if re.search(r"[^aeiouy]{4,}", s.replace("ch", "C").replace("sh", "S").replace("th", "T").replace("ng", "N")):
        return None
    out: list[tuple[int, str]] = []  # (index of the spelling, phonemes)
    i, n = 0, len(s)
    while i < n:
        rest = s[i:]
        if rest == "e" and i > 0 and any(c in vowels for c in s[:i]):
            # final silent e, lengthening the vowel before a single consonant (vite, kube)
            if i >= 2 and s[i - 1] not in vowels and s[i - 2] in vowels and (i < 3 or s[i - 3] not in vowels):
                out = [(k, _LTS_LONG[s[k]] if k == i - 2 else p) for k, p in out]
            break
        if rest == "le" and i > 0 and s[i - 1] not in vowels:
            out.append((i, "ᵊl"))
            break
        for g, p in _LTS_GROUPS:
            if rest.startswith(g):
                out.append((i, p))
                i += len(g)
                break
        else:
            c = s[i]
            if c in "cg" and i + 1 < n and s[i + 1] in "eiy":
                p = "s" if c == "c" else "ʤ"
            elif c == "y" and i == 0:
                p = "j"
            elif c == "x" and i == 0:
                p = "z"
            elif c in vowels and i == n - 1 and i > 0:
                p = _LTS_FINAL[c]
            elif i > 0 and c == s[i - 1] and c not in vowels:
                p = ""  # doubled consonant
            else:
                p = _LTS_ONE[c]
            out.append((i, p))
            i += 1
    ps = "".join(p for _, p in out)
    k = next((k for k, c in enumerate(ps) if c in _VOWELS), None)
    if k is None:
        return None
    if ps[k] == "ə":
        ps = ps[:k] + "ɜ" + ps[k + 1 :] if ps[k + 1 : k + 2] == "ɹ" else ps[:k] + "ʌ" + ps[k + 1 :]
    return ps[:k] + "ˈ" + ps[k:]


# -- reading code ---------------------------------------------------------------------------------

# How file extensions are said; other words from the word list are resolved through it.
_EXTS = {
    "py": "pie",
    "pyi": "pyi",
    "pyc": "pyc",
    "ipynb": "ipynb",
    "js": "J S",
    "mjs": "M J S",
    "cjs": "C J S",
    "ts": "T S",
    "mts": "M T S",
    "cts": "C T S",
    "tsx": "T S X",
    "jsx": "J S X",
    "json": "jason",
    "jsonl": "jsonl",
    "yaml": "yaml",
    "yml": "yml",
    "toml": "toml",
    "md": "M D",
    "mdx": "M D X",
    "rs": "R S",
    "go": "go",
    "sh": "S H",
    "zsh": "Z S H",
    "bash": "bash",
    "fish": "fish",
    "txt": "text",
    "csv": "C S V",
    "tsv": "T S V",
    "html": "H T M L",
    "htm": "H T M",
    "css": "C S S",
    "scss": "S C S S",
    "sass": "sass",
    "less": "less",
    "c": "C",
    "h": "H",
    "cc": "C C",
    "cpp": "C P P",
    "hpp": "H P P",
    "java": "java",
    "kt": "K T",
    "kts": "K T S",
    "swift": "swift",
    "rb": "R B",
    "php": "P H P",
    "sql": "S Q L",
    "lock": "lock",
    "lockb": "lock B",
    "env": "env",
    "cfg": "C F G",
    "conf": "/kˈɑnf/",
    "ini": "I N I",
    "xml": "X M L",
    "svg": "S V G",
    "png": "P N G",
    "jpg": "/ʤˈApˌɛɡ/",
    "jpeg": "/ʤˈApˌɛɡ/",
    "gif": "gif",
    "webp": "web P",
    "ico": "I C O",
    "pdf": "P D F",
    "wav": "wave",
    "mp3": "M P 3",
    "mp4": "M P 4",
    "mov": "M O V",
    "webm": "web M",
    "log": "log",
    "lua": "lua",
    "ex": "E X",
    "exs": "E X S",
    "dart": "dart",
    "vue": "view",
    "svelte": "svelte",
    "astro": "astro",
    "rst": "R S T",
    "tex": "tech",
    "wasm": "wasm",
    "so": "S O",
    "dylib": "/dˈIlɪb/",
    "dll": "D L L",
    "exe": "E X E",
    "zip": "zip",
    "gz": "G Z",
    "tgz": "T G Z",
    "tar": "tar",
    "whl": "wheel",
    "pkl": "pickle",
    "pt": "P T",
    "pth": "P T H",
    "onnx": "onnx",
    "gguf": "gguf",
    "safetensors": "safetensors",
    "bin": "bin",
    "db": "D B",
    "sqlite": "sqlite",
    "proto": "proto",
    "graphql": "graphql",
    "tf": "T F",
    "hcl": "H C L",
    "nix": "nix",
    "mod": "mod",
    "sum": "sum",
    "plist": "P list",
    "jar": "jar",
    "cs": "C S",
    "fs": "F S",
    "r": "R",
    "jl": "J L",
    "pl": "P L",
    "hs": "H S",
    "ml": "M L",
    "clj": "C L J",
    "erl": "E R L",
    "kdl": "K D L",
    "ps1": "P S 1",
    "bat": "bat",
    "cmd": "C M D",
    "map": "map",
    "out": "out",
    "o": "O",
    "a": "A",
    "gradle": "gradle",
    "pem": "P E M",
    "crt": "C R T",
    "key": "key",
    "csr": "C S R",
    "patch": "patch",
    "diff": "diff",
    "orig": "orig",
    "bak": "back",
    "tmp": "temp",
    "old": "old",
    "sample": "sample",
    "example": "example",
    "service": "service",
    "socket": "socket",
    "timer": "timer",
    "rules": "rules",
}
_EXT_RE = "|".join(sorted((re.escape(e) for e in _EXTS), key=len, reverse=True))

_OPS = {
    "===": "triple equals",
    "!==": "not equals",
    "**=": "power equals",
    "...": "dot dot dot",
    "..=": "dot dot equals",
    "<=>": "spaceship",
    ">>>": "unsigned shift right",
    "<<=": "shift left equals",
    ">>=": "shift right equals",
    "??=": "nullish equals",
    "||=": "or equals",
    "&&=": "and equals",
    "==": "double equals",
    "!=": "not equals",
    "<=": "less than or equal to",
    ">=": "greater than or equal to",
    "=>": "arrow",
    "->": "arrow",
    "<-": "left arrow",
    "&&": "and",
    "||": "or",
    "++": "plus plus",
    "--": "minus minus",
    "+=": "plus equals",
    "-=": "minus equals",
    "*=": "times equals",
    "/=": "divide equals",
    "%=": "mod equals",
    "^=": "xor equals",
    "|=": "or equals",
    "&=": "and equals",
    "**": "to the power of",
    "//": "slash slash",
    "::": "colon colon",
    ":=": "colon equals",
    "..": "dot dot",
    "?.": "question dot",
    "??": "double question mark",
    "<<": "shift left",
    ">>": "shift right",
    "|>": "pipe",
    "<>": "not equals",
    "=": "equals",
    "<": "less than",
    ">": "greater than",
    "+": "plus",
    "-": "minus",
    "*": "times",
    "/": "slash",
    "%": "mod",
    "^": "xor",
    "~": "tilde",
    "!": "not",
    "?": "question mark",
    "&": "and",
    "|": "pipe",
    "@": "at",
    "#": "hash",
    "$": "dollar",
    "\\": "backslash",
    ":": "colon",
    ".": "dot",
    "→": "to",
    "←": "from",
    "⇒": "implies",
    "≤": "less than or equal to",
    "≥": "greater than or equal to",
    "≠": "not equal to",
    "≈": "about",
    "×": "times",
    "±": "plus or minus",
    "∞": "infinity",
}
_OP_RE = "|".join(re.escape(o) for o in sorted(_OPS, key=len, reverse=True))

_CODE_TOKEN = re.compile(
    rf"""
      (?P<url>(?:https?|ftp|wss?)://[^\s'"`<>]+)
    | (?P<path>(?:~|\.{{1,2}})?/[\w.~@%+*/-]*|[\w.@%+~-]+(?:/[\w.@%+*~-]+)+/?)
    | (?P<file>(?:[\w-]+|(?<![\w.])(?=\.))(?:\.[\w-]+)*\.(?:{_EXT_RE})(?![\w-]))
    | (?P<flag>(?<![\w-])--?[A-Za-z][\w-]*)
    | (?P<hex>0[xX][0-9A-Fa-f]+(?![\w]))
    | (?P<ver>v?\d+(?:\.\d+)+(?:-[A-Za-z]+\.?\d*)?(?![\w.]))
    | (?P<num>\d[\d_]*[A-Za-z%µμ]*)
    | (?P<str>"[^"]*"|'[^']*')
    | (?P<word>[^\W\d][\w]*(?:-[^\W\d][\w]*)*)
    | (?P<op>{_OP_RE}|\S)
    | (?P<space>\s+)
    """,
    re.X,
)
_OPEN = {"(": ")", "[": "]", "{": "}"}


def split_identifier(word: str) -> str:
    """`useEffect` -> `use effect`, `HF_HUB_OFFLINE` -> `HF hub offline`, `__init__` -> `dunder init`."""
    dunder = re.fullmatch(r"__(\w+?)__", word)
    if dunder:
        return "dunder " + split_identifier(dunder.group(1))
    out: list[str] = []
    for part in re.split(r"[_\-]+", word.strip("_-")):
        for p in _pieces(part) or [part]:
            out.append(p if p.isupper() and len(p) <= 4 else p.lower())
    return " ".join(w for w in out if w)


def _say_identifier(word: str, lexicon: dict[str, str] | None = None) -> str:
    """An identifier in code: split into words, each said the way misaki can say it."""
    if lexicon and (hit := _user_word(word, lexicon)) is not None:
        return hit
    if (hit := _listed(word)) is not None:
        return hit
    if word in ("a", "A"):
        return spell(word)  # a variable, not the article
    out = []
    for w in split_identifier(word).split():
        if w.startswith("dunder"):
            out.append(w)
        elif lexicon and (hit := _user_word(w, lexicon)) is not None:
            out.append(hit)
        else:
            out.append(say_word(w) or w)
    return " ".join(out)


def _say_number(num: str) -> str:
    """`5ms`, `200MB`, `3x`, `1_000`: numbers with units glued on, as words misaki reads."""
    num = num.replace("_", "")
    if (said := _tight_unit(num)) is not None:
        return said
    m = re.fullmatch(r"(\d+)([A-Za-z]+)", num)
    if m and m.group(2) not in ("st", "nd", "rd", "th", "s"):  # 3rd and 1990s read as they are
        return m.group(1) + " " + (say_word(m.group(2)) or spell(m.group(2)))
    return num


def _say_version(v: str) -> str:
    """`v2.3.1` -> version 2 point 3 point 1; `127.0.0.1` -> 1 2 7 dot 0 dot 0 dot 1."""
    m = re.fullmatch(r"(v?)(\d+(?:\.\d+)+)(?:-([A-Za-z]+)\.?(\d*))?", v)
    if not m:
        return v
    parts = m.group(2).split(".")
    if len(parts) == 4 and not m.group(1) and all(len(p) <= 3 for p in parts):
        return " dot ".join(" ".join(p) for p in parts)  # an IP address
    words = " point ".join(" ".join(p) if len(p) > 1 and p.startswith("0") else p for p in parts)
    if m.group(1):
        words = "version " + words
    if m.group(3):
        tag = m.group(3)
        words += " " + (say_word(tag) or (spell(tag) if len(tag) <= 2 else tag)) + (" " + m.group(4) if m.group(4) else "")
    return words


def _say_flag(flag: str) -> str:
    """`--dry-run` -> dash dash dry run, `-rf` -> dash R F."""
    dashes = len(flag) - len(flag.lstrip("-"))
    body, _, value = flag.lstrip("-").partition("=")
    if dashes == 1 and len(body) <= 3 and body.isalpha() and (len(body) < 3 or not _lookup(body)):
        said = spell(body)
    else:
        said = _say_identifier(body)
    out = " ".join(["dash"] * dashes + [said])
    if value:
        out += " equals " + speak_code(value)
    return out


def _say_hex(h: str) -> str:
    """`0x1F` -> hex 1 F."""
    return "hex " + " ".join(c if c.isdigit() else spell(c.upper()) for c in h[2:])


def _say_file(name: str) -> str:
    """`docker-compose.yaml` -> docker compose dot yaml, `.env` -> dot env, `__init__.py` -> dunder init dot pie."""
    parts = name.split(".")
    out = [_say_identifier(parts[0])] if parts[0] else []
    for p in parts[1:]:
        if not p:
            continue
        ext = _EXTS.get(p.lower()) if p is parts[-1] else None
        if ext is not None:
            said = _render(ext, p)
        else:
            said = _say_identifier(p)
        out.append("dot " + said)
    return " ".join(out)


def _say_path(path: str) -> str:
    """`~/.config/app/settings.json` -> tilde slash dot config slash app slash settings dot json."""
    out: list[str] = []
    trailing = path.endswith("/") and len(path) > 1
    segs = path.rstrip("/").split("/") if len(path) > 1 else ["", ""]
    for k, seg in enumerate(segs):
        if k:
            out.append("slash")
        if seg == "~":
            out.append("tilde")
        elif seg in (".", ".."):
            out.append("dot" if seg == "." else "dot dot")
        elif seg in ("*", "**"):
            out.append("star" if seg == "*" else "star star")
        elif seg:
            out.append(_say_file(seg) if "." in seg.strip(".") or seg.startswith(".") else _say_identifier(seg))
    if trailing:
        out.append("slash")
    return " ".join(out)


_TLDS = "com|org|net|io|dev|ai|app|co|sh|me|gg|xyz|edu|gov|uk|de|so|tech|cloud|run|page|site|info|us|ca|ly|to|fm|tv|rs|js|cc"


def _say_url(url: str) -> str:
    """`https://docs.example.com/guide?q=1` -> docs dot example dot com slash guide."""
    m = re.match(r"(?:[a-z]+://)?(?:[^@/]+@)?([^/?#:]*)(?::(\d+))?([^?#]*)", url)
    host, port, path = m.group(1), m.group(2), m.group(3).strip("/")
    out = [" dot ".join(_say_host_part(p) for p in host.split(".") if p)]
    if port:
        out.append("colon " + port)
    if path:
        out.append("slash " + _say_path(path))
    return " ".join(x for x in out if x)


def _say_host_part(p: str) -> str:
    if p.lower() == "www":
        return "W W W"
    if p.lower() in ("io", "ai", "sh", "gg", "rs", "js", "cc", "tv", "fm", "ly", "us", "uk", "de", "ca"):
        return spell(p.upper())
    return _say_identifier(p)


def speak_code(code: str, lexicon: dict[str, str] | None = None) -> str:
    """Read a short code span aloud: words for identifiers, names for symbols."""
    s = code.strip()
    if not s:
        return ""
    if lexicon and (hit := _user_word(s, lexicon)) is not None:
        return hit
    if (hit := _listed(s)) is not None:
        return hit
    toks = [(m.lastgroup, m.group()) for m in _CODE_TOKEN.finditer(s)]
    sig = [k for k, (kind, _) in enumerate(toks) if kind != "space"]  # significant token indices
    out: list[str] = []
    skip: set[int] = set()
    for pos, k in enumerate(sig):
        if k in skip:
            continue
        kind, t = toks[k]
        prev = toks[sig[pos - 1]] if pos else None
        nxt = toks[sig[pos + 1]] if pos + 1 < len(sig) else None
        spaced_before = k > 0 and toks[k - 1][0] == "space"
        spaced_after = k + 1 < len(toks) and toks[k + 1][0] == "space"
        prev_is_value = prev is not None and (prev[0] in ("word", "num", "ver", "str", "hex") or prev[1] in ")]}")
        if kind == "url":
            out.append(_say_url(t))
        elif kind == "path":
            if t == "/" and spaced_before and spaced_after:
                out.append("divided by")
            else:
                out.append(_say_path(t))
        elif kind == "file":
            out.append(_say_file(t))
        elif kind == "flag":
            out.append(_say_flag(t))
        elif kind == "hex":
            out.append(_say_hex(t))
        elif kind == "ver":
            out.append(_say_version(t))
        elif kind == "num":
            out.append(_say_number(t))
        elif kind == "str":
            inner = t[1:-1]
            out.append(speak_code(inner, lexicon) if inner.strip() else "empty string")
        elif kind == "word":
            out.append(_say_identifier(t, lexicon))
        elif t in _OPEN:
            close = _OPEN[t]
            if nxt is not None and nxt[1] == close:  # foo(), [], {}
                skip.add(sig[pos + 1])
                if not prev_is_value:
                    out.append({"(": "", "[": "empty list", "{": "empty braces"}[t])
            elif prev_is_value and not spaced_before and t in "([":
                out.append("of")  # f(x), list[int]
        elif t in ")]}":
            continue
        elif t == "<" and prev is not None and prev[0] == "word" and not spaced_before and _generic_close(toks, k):
            out.append("of")  # Vec<T>
            skip.add(_generic_close(toks, k))
        elif t in ("*", "**") and not prev_is_value:
            out.append("star" if t == "*" else "star star")  # *args, **kwargs
        elif t == "&" and not prev_is_value:
            out.append("ref")  # &str
        elif t == "!" and prev_is_value and not spaced_before:
            out.append("bang")  # println!
        elif t == "-" and not prev_is_value:
            out.append("minus")
        elif t == "^" and not spaced_before and nxt is not None and nxt[1] in ("2", "3"):
            out.append("squared" if nxt[1] == "2" else "cubed")
            skip.add(sig[pos + 1])
        elif t == "|" and nxt is not None and (nxt[1][:1].isupper() or nxt[1] in ("null", "undefined")):
            out.append("or")  # str | None
        elif t in (",", ";", "'", '"', "`"):
            if t == "," and out:
                out[-1] += ","
        else:
            out.append(_OPS.get(t, t))
    return re.sub(r"\s+", " ", " ".join(w for w in out if w)).strip(" ,")


def _generic_close(toks: list[tuple[str, str]], k: int) -> int:
    """Index of the `>` closing a generic `<` at k, or 0 when this `<` is a comparison."""
    depth = 0
    for i in range(k, len(toks)):
        kind, t = toks[i]
        if t == "<":
            depth += 1
        elif t == ">":
            depth -= 1
            if depth == 0:
                return i
        elif kind == "space" or kind == "word" or t in (",", "::", "&", "'", "(", ")", "[", "]"):
            continue
        else:
            return 0
    return 0


def _user_word(word: str, lexicon: dict[str, str]) -> str | None:
    """The user's word list: exact match, or any case for an all-lowercase entry."""
    value = lexicon.get(word)
    if value is None:
        value = next((v for k, v in lexicon.items() if k.islower() and k == word.lower()), None)
    return None if value is None else _user_value(value, word)


def _user_value(value: str, word: str) -> str:
    """A user word-list value as spoken text: used as written, or `/phonemes/` said inline."""
    return _render(value, word) if len(value) > 2 and value.startswith("/") and value.endswith("/") else value


# -- reading prose --------------------------------------------------------------------------------

_UNITS = {  # as written: (singular, plural)
    "ns": ("nanosecond", "nanoseconds"),
    "µs": ("microsecond", "microseconds"),
    "μs": ("microsecond", "microseconds"),
    "us": ("microsecond", "microseconds"),
    "ms": ("millisecond", "milliseconds"),
    "s": ("second", "seconds"),
    "sec": ("second", "seconds"),
    "secs": ("seconds", "seconds"),
    "min": ("minute", "minutes"),
    "mins": ("minutes", "minutes"),
    "h": ("hour", "hours"),
    "hr": ("hour", "hours"),
    "hrs": ("hours", "hours"),
    "KB": ("kilobyte", "kilobytes"),
    "kB": ("kilobyte", "kilobytes"),
    "MB": ("megabyte", "megabytes"),
    "GB": ("gigabyte", "gigabytes"),
    "TB": ("terabyte", "terabytes"),
    "PB": ("petabyte", "petabytes"),
    "KiB": ("kibibyte", "kibibytes"),
    "MiB": ("mebibyte", "mebibytes"),
    "GiB": ("gibibyte", "gibibytes"),
    "TiB": ("tebibyte", "tebibytes"),
    "Kb": ("kilobit", "kilobits"),
    "Mb": ("megabit", "megabits"),
    "Gb": ("gigabit", "gigabits"),
    "kbps": ("kilobit per second", "kilobits per second"),
    "Kbps": ("kilobit per second", "kilobits per second"),
    "Mbps": ("megabit per second", "megabits per second"),
    "Gbps": ("gigabit per second", "gigabits per second"),
    "KB/s": ("kilobyte per second", "kilobytes per second"),
    "MB/s": ("megabyte per second", "megabytes per second"),
    "GB/s": ("gigabyte per second", "gigabytes per second"),
    "Hz": ("hertz", "hertz"),
    "kHz": ("kilohertz", "kilohertz"),
    "MHz": ("megahertz", "megahertz"),
    "GHz": ("gigahertz", "gigahertz"),
    "fps": ("FPS", "FPS"),
    "px": ("pixel", "pixels"),
    "dpi": ("DPI", "DPI"),
    "pt": ("point", "points"),
    "dB": ("decibel", "decibels"),
    "x": ("times", "times"),
    "×": ("times", "times"),
    "M": ("million", "million"),
    "B": ("billion", "billion"),
    "rps": ("request per second", "requests per second"),
    "qps": ("query per second", "queries per second"),
    "req/s": ("request per second", "requests per second"),
    "tok/s": ("token per second", "tokens per second"),
    "ops/s": ("operation per second", "operations per second"),
    "LOC": ("line of code", "lines of code"),
    "W": ("watt", "watts"),
    "mAh": ("milliamp hour", "milliamp hours"),
    "°C": ("degree Celsius", "degrees Celsius"),
    "°F": ("degree Fahrenheit", "degrees Fahrenheit"),
}
# Single letters only right after the number (5s, 3x, 7B); longer units may follow a space (5 ms).
_TIGHT_ONLY = {"s", "h", "x", "M", "B", "W", "×"}
_NUM = r"\d+(?:[.,]\d+)*(?:[-–]\d+(?:[.,]\d+)*)?"
_UNIT_ALL = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_UNIT_SPACED = "|".join(re.escape(u) for u in sorted(set(_UNITS) - _TIGHT_ONLY, key=len, reverse=True))
_UNIT_TIGHT = re.compile(rf"({_NUM})({_UNIT_ALL})")


def _tight_unit(text: str) -> str | None:
    """`5ms` -> 5 milliseconds, or None when `text` is not a number with a unit glued on."""
    m = _UNIT_TIGHT.fullmatch(text)
    if not m or re.fullmatch(r"\d{3}0s", text):  # 1990s is a decade
        return None
    return _unit(m.group(1), m.group(2))


def _unit(number: str, unit: str) -> str:
    one, many = _UNITS[unit]
    number = re.sub(r"(\d)[-–](\d)", r"\1 to \2", number)
    return f"{number} {one if number == '1' else many}"


_PRODUCTS = (
    "Python|Node|Node.js|Java|Ruby|Go|Rust|PHP|Perl|Swift|Kotlin|Scala|Elixir|Erlang|Julia|Lua|TypeScript|"
    "JavaScript|ECMAScript|ES|React|Vue|Angular|Svelte|Django|Rails|Laravel|Spring|macOS|iOS|Android|Ubuntu|"
    "Debian|Fedora|Windows|Linux|kernel|Postgres|PostgreSQL|MySQL|Redis|MongoDB|Kubernetes|Docker|CUDA|Xcode|"
    "Chrome|Firefox|Safari|Electron|Gradle|npm|pip|uv|HTTP|TLS|SSL|OAuth|Unicode|Bootstrap|Tailwind|Llama|GPT|"
    "Claude|Gemini|Qwen|Mistral|Sonnet|Opus|Haiku|Kokoro|Whisper|NumPy|pandas|PyTorch|TensorFlow|version|"
    "release|Version|Release"
)
# Rules that may span spaces; everything else is decided one whitespace-separated chunk at a time.
_SPAN_RE = re.compile(
    rf"""
      (?P<bigo>(?<![\w])[OΘΩ]\((?:[^()\s]|\s(?=[^()]*\))|\([^()]*\))+\))
    | (?P<unit>(?<![\w.~≈])[~≈]?{_NUM}[ ](?:{_UNIT_SPACED})(?![\w/]))
    | (?:\b(?:{_PRODUCTS})[ ](?P<prodver>\d+\.\d+)(?![\w.]))
    """,
    re.X,
)
_LEAD = "([{\"'“‘«"
_TRAIL = ".,;:!?)]}\"'”’»…"


def _say_bigo(t: str) -> str:
    """`O(n log n)` -> order n log n, `O(n^2)` -> order n squared, `Θ(n)` -> theta of n.

    "Order n" is as common in speech as "big O of n" and came through speech recognition
    every time, where "big O of n log n" often lost everything after the O."""
    head, inner = t[0], t[2:-1]
    out = {"O": ["order"], "Θ": ["theta", "of"], "Ω": ["omega", "of"]}[head]
    for m in re.finditer(r"\^\s*\(?([\w]+)\)?|[²³]|sqrt|√|log|lg|ln|\d+|[A-Za-z]+|[*·×+\-/!]", inner):
        g = m.group()
        if g.startswith("^"):
            e = m.group(1)
            out.append("squared" if e == "2" else "cubed" if e == "3" else f"to the {e}")
        elif g in ("²", "³"):
            out.append("squared" if g == "²" else "cubed")
        elif g in ("sqrt", "√"):
            out.append("square root of")
        elif g in ("log", "lg", "ln"):
            out.append("log")
        elif g.isalpha():
            # variables as written: a lowercase n reads more clearly here than N
            out.append(" ".join(spell(c) if c in "aA" else c for c in g) if not _lookup(g) or len(g) <= 2 else g)
        else:
            out.append({"*": "times", "·": "times", "×": "times", "+": "plus", "-": "minus", "/": "over", "!": "factorial"}.get(g, g))
    return " ".join(out)


def _say_chunk(core: str, lexicon: dict[str, str] | None) -> str | None:
    """Spoken text for one chunk of prose, stripped of surrounding punctuation, or None to keep it."""
    if lexicon and (hit := _user_word(core, lexicon)) is not None:
        return hit
    if (hit := _listed(core)) is not None:
        return hit
    if _lookup(core) or re.fullmatch(r"(?:[A-Za-z]\.){2,}|[A-Za-z]{1,5}\.", core):
        return None  # a dictionary entry as written, or an abbreviation (e.g., etc.)
    if core[:1] in "~≈" and core[1:2].isdigit():
        rest = core[1:]
        return "about " + (_say_chunk(rest, lexicon) or rest)
    if re.fullmatch(r"(?:https?|ftp|wss?)://\S+", core):
        return _say_url(core)
    if re.fullmatch(rf"(?:[\w-]+\.)+(?:{_TLDS})(?::\d+)?(?:/\S*)?|localhost(?::\d+)(?:/\S*)?", core):
        return _say_url(core)
    m = re.fullmatch(r"([\w.+-]+)@([\w-]+(?:\.[\w-]+)+)", core)
    if m:
        return _say_identifier(m.group(1).replace(".", " dot ")) + " at " + _say_url(m.group(2))
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?", core):
        ip, _, port = core.partition(":")
        return _say_version(ip) + (" colon " + port if port else "")
    if re.fullmatch(r"v\d+(?:\.\d+)*(?:-[A-Za-z]+\.?\d*)?|\d+\.\d+\.\d+(?:-[A-Za-z]+\.?\d*)?", core):
        return _say_version(core) if "." in core else "version " + core[1:]
    if (said := _tight_unit(core)) is not None:
        return said
    if re.fullmatch(r"#\d+", core):
        return "number " + core[1:]
    if re.fullmatch(r"0[xX][0-9A-Fa-f]+", core):
        return _say_hex(core)
    if re.fullmatch(r"[OΘΩ]\(.+\)", core):
        return _say_bigo(core)
    if "/" in core and re.fullmatch(r"[\w.~@%+*/-]+", core):
        is_path = (
            core.startswith(("/", "~/", "./", "../")) or core.endswith("/") or core.count("/") >= 2 or re.search(rf"\.(?:{_EXT_RE})$", core)
        )
        if is_path:
            return _say_path(core)
        sides = core.split("/")  # and/or, TCP/IP, HTTP/2: misaki reads the sides, slash unsaid
        said = [_say_chunk(p, lexicon) if p else None for p in sides]
        return None if all(x is None for x in said) else " ".join(x or p for x, p in zip(said, sides, strict=True))
    if re.fullmatch(rf"\.?[\w-]+(?:\.[\w-]+)*\.(?:{_EXT_RE})|\.[A-Za-z][\w.-]*", core):
        return _say_file(core)
    if re.fullmatch(r"--?[A-Za-z][\w-]*(?:=\S+)?", core):
        return _say_flag(core)
    if re.fullmatch(r"(?=.*\w\w)[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+(?:\(\))?|[A-Za-z_]\w*\(\)|[A-Za-z_][\w.-]*=\S+", core):
        return speak_code(core, lexicon)  # os.path.join, render(), port=8080
    if core in _OPS and core not in ("--", "..", "...") and (len(core) > 1 or core in "=<>→←⇒≤≥≠≈×±"):
        return _OPS[core]  # but -- and ... in prose are punctuation
    if re.fullmatch(r"[\w'’-]+", core):
        return _say_words(core)
    return None


def _say_words(chunk: str) -> str | None:
    """A prose word, maybe hyphenated or snake_case: only the pieces misaki would get wrong change."""
    parts = [p for p in re.split(r"[-_]+", chunk) if p]
    said = [say_word(p) for p in parts]
    if all(s is None for s in said):
        return None
    return " ".join(s or p for s, p in zip(said, parts, strict=True))


def _prose(text: str, lexicon: dict[str, str] | None) -> list[tuple[int, int, str]]:
    """Rewrites for a stretch of prose, as (start, end, spoken) in `text` offsets."""
    marks: list[tuple[int, int, str]] = []
    pos = 0
    for m in _SPAN_RE.finditer(text):
        marks += _prose_chunks(text, pos, m.start(), lexicon)
        if m.group("bigo"):
            marks.append((m.start(), m.end(), _say_bigo(m.group())))
        elif m.group("unit"):
            t = m.group()
            about = "about " if t[0] in "~≈" else ""
            n = re.match(_NUM, t.lstrip("~≈")).group()
            marks.append((m.start(), m.end(), about + _unit(n, t.lstrip("~≈")[len(n) :].strip())))
        else:
            a, b = m.span("prodver")
            marks += _prose_chunks(text, m.start(), a, lexicon)
            marks.append((a, b, _say_version(m.group("prodver"))))
        pos = m.end()
    return marks + _prose_chunks(text, pos, len(text), lexicon)


def _prose_chunks(text: str, a: int, b: int, lexicon: dict[str, str] | None) -> list[tuple[int, int, str]]:
    marks = []
    for m in re.finditer(r"\S+", text[a:b]):
        s, e = a + m.start(), a + m.end()
        while s < e and text[s] in _LEAD:
            s += 1
        while e > s and text[e - 1] in _TRAIL and not (text[e - 1] == ")" and text.count("(", s, e) >= text.count(")", s, e)):
            e -= 1
        if s >= e or re.fullmatch(r"(?:[A-Za-z]\.){2,}", text[s : e + 1]):
            continue  # e.g., i.e.: misaki reads these
        said = _say_chunk(text[s:e], lexicon)
        if said is not None and said != text[s:e]:
            marks.append((s, e, said))
    return marks


def _rescue(text: str) -> str:
    """Fill in only the words misaki would skip in silence; used for explicit `say` text."""

    def fix(m: re.Match) -> str:
        w = m.group()
        if all(known(p) or p.isdigit() for p in _pieces(w)):
            return w
        return say_word(w) or w

    parts = re.split(r"(\[[^\]]+\]\([^)]*\))", text)  # keep inline pronunciations as given
    return "".join(p if i % 2 else re.sub(r"[A-Za-z][\w'’]*", fix, p) for i, p in enumerate(parts))


def _plural_reading(said: str) -> str:
    """Add -s to the last word of a reading (`struct`s, `Vec`s)."""
    m = _INLINE_RE.search(said)
    if m and m.end() == len(said):
        return said[: m.start()] + _inline(m.group(1) + "s", _plural(m.group(2)))
    last = said.split()[-1] if said.split() else ""
    if not last.isalpha() or last.isupper() or len(last) == 1:
        return said + "'s"
    return said + "s"


# -- the entry point ------------------------------------------------------------------------------


def speakable(
    display: str,
    code: list[tuple[int, int]] | tuple = (),
    say: list[tuple[int, int, str]] | tuple = (),
    lexicon: dict[str, str] | None = None,
    british: bool = False,
) -> Spoken:
    """Spoken text for narration `display`, with segments mapping it back to display offsets.

    `code` are display ranges written in backticks, `say` explicit `(start, end, spoken)`
    overrides (they always win), `lexicon` the user's word list {word: spoken}. With
    `british`, the pronunciations given as phonemes are converted for a British voice.
    """
    marks: list[tuple[int, int, str]] = [(a, b, _rescue(spoken)) for a, b, spoken in say]
    given = {(a, b) for a, b, _ in marks}  # phonemes in `say` text are kept as written

    def free(a: int, b: int) -> bool:
        return not any(a < tb and ta < b for ta, tb, _ in marks)

    for a, b in code:  # code reads the user's word list itself
        if a < b and free(a, b):
            said = speak_code(display[a:b], lexicon)
            m = re.match(r"(?:'s|’s|es|s)(?!\w)", display[b:])
            if m and said and free(b, b + m.end()):  # `struct`s
                said, b = _plural_reading(said), b + m.end()
            marks.append((a, b, said))
    if lexicon:
        for word, spoken in lexicon.items():
            flags = re.I if word.islower() else 0
            for m in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", display, flags):
                if free(m.start(), m.end()):
                    marks.append((m.start(), m.end(), _user_value(spoken, m.group())))
    marks.sort()
    gaps, pos = [], 0
    for a, b, _ in marks:
        if a > pos:
            gaps.append((pos, a))
        pos = max(pos, b)
    gaps.append((pos, len(display)))
    for a, b in gaps:
        marks += [(a + s, a + e, said) for s, e, said in _prose(display[a:b], lexicon)]
    marks.sort()

    def accent(m: re.Match) -> str:
        """The word list marks the vowel of "father" with ː, which only British voices keep."""
        ps = m.group(2)
        return _inline(m.group(1), to_british(ps) if british else ps.replace("ː", ""))

    out = _Builder()
    pos = 0
    for a, e, spoken in marks:
        if a < pos:
            continue
        if (a, e) not in given:
            spoken = _INLINE_RE.sub(accent, spoken)
        out.add(display[pos:a], pos, a, copied=True)
        out.add(spoken, a, e)
        pos = e
    out.add(display[pos:], pos, len(display), copied=True)
    return out.build()
