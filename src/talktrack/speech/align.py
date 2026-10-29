"""Word timings for a voice that reports none (VoxCPM2), from the audio alone.

Each sentence is voiced on its own, so its start and end are known. Inside a sentence the
words are spread over the voiced stretch by length, and where the text has a clause break
(a comma or similar) the longest pauses in the audio are taken as those breaks. That is
close enough for the highlighted word and for cues, with no alignment model to load.
"""

from __future__ import annotations

import re

import numpy as np

from .base import SAMPLE_RATE, Token

FRAME_S = 0.02  # loudness is measured in 20 ms frames
SILENCE = 0.1  # a frame quieter than this share of the sentence's loud frames is silent
MIN_PAUSE_S = 0.12  # shorter quiet stretches are part of a word
WORD_RE = re.compile(r"[\w\u064B-\u0652\u0670'’\-]+")  # harakat are part of a word
CLAUSE_RE = re.compile(r"[،,؛;:\-–—]\s")


def frame_loudness(audio: np.ndarray) -> np.ndarray:
    n = max(1, int(FRAME_S * SAMPLE_RATE))
    frames = len(audio) // n
    if frames == 0:
        return np.zeros(1, dtype=np.float32)
    x = audio[: frames * n].reshape(frames, n)
    return np.sqrt((x.astype(np.float32) ** 2).mean(axis=1))


def voiced_span(loud: np.ndarray) -> tuple[float, float]:
    """Start and end, in seconds, of the sound inside a clip (its silence trimmed)."""
    on = np.flatnonzero(loud > _floor(loud))
    if not len(on):
        return 0.0, len(loud) * FRAME_S
    return on[0] * FRAME_S, (on[-1] + 1) * FRAME_S


def pauses(loud: np.ndarray, a: float, b: float) -> list[tuple[float, float]]:
    """Quiet stretches of at least MIN_PAUSE_S between seconds `a` and `b`."""
    quiet = loud <= _floor(loud)
    out, start = [], None
    i0, i1 = int(a / FRAME_S), int(b / FRAME_S)
    for i in range(i0, min(i1, len(quiet))):
        if quiet[i] and start is None:
            start = i
        elif not quiet[i] and start is not None:
            if (i - start) * FRAME_S >= MIN_PAUSE_S:
                out.append((start * FRAME_S, i * FRAME_S))
            start = None
    return out


def _floor(loud: np.ndarray) -> float:
    peak = float(np.percentile(loud, 90)) if len(loud) else 0.0
    return SILENCE * peak


def sentence_tokens(text: str, audio: np.ndarray, offset: float) -> list[Token]:
    """Timed words of one voiced sentence that starts `offset` seconds into the paragraph."""
    words = list(WORD_RE.finditer(text))
    if not words:
        return []
    loud = frame_loudness(audio)
    a, b = voiced_span(loud)
    # clauses: runs of words between the text's clause breaks
    breaks = [m.end() for m in CLAUSE_RE.finditer(text)]
    clauses: list[list[re.Match]] = [[]]
    for w in words:
        while breaks and w.start() >= breaks[0]:
            breaks.pop(0)
            if clauses[-1]:
                clauses.append([])
        clauses[-1].append(w)
    spans = _clause_spans(clauses, pauses(loud, a, b), a, b)
    tokens: list[Token] = []
    for clause, (ca, cb) in zip(clauses, spans, strict=True):
        weights = [len(w.group()) + 1 for w in clause]
        total = sum(weights)
        t = ca
        for w, wt in zip(clause, weights, strict=True):
            d = (cb - ca) * wt / total
            tokens.append(Token(w.group(), round(offset + t, 3), round(offset + t + d * 0.92, 3)))
            t += d
    return tokens


def _clause_spans(clauses: list[list], gaps: list[tuple[float, float]], a: float, b: float) -> list[tuple[float, float]]:
    """Time span of each clause: split at the longest pauses when there are enough of them,
    otherwise by the clauses' share of the letters."""
    need = len(clauses) - 1
    if need and len(gaps) >= need:
        chosen = sorted(sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:need])
        edges = [a] + [x for g in chosen for x in g] + [b]
        return [(edges[2 * i], edges[2 * i + 1]) for i in range(len(clauses))]
    sizes = [sum(len(w.group()) + 1 for w in c) for c in clauses]
    total = sum(sizes)
    out, t = [], a
    for s in sizes:
        d = (b - a) * s / total
        out.append((t, t + d))
        t += d
    return out
