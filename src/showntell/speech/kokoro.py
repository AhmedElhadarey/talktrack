"""What the two Kokoro engines share: text to phonemes, chunks, input ids and word timings.

Phonemes come from misaki's English G2P, with espeak-ng for unknown words only when the
`espeak` extra is installed. Chunking, the style vector per chunk and word timings follow
Kokoro's own pipeline (`kokoro.KPipeline`), so both engines voice and time words the same way.
An engine only loads its weights and runs the model on one chunk (`_run`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import numpy as np

from .base import SAMPLE_RATE, Token

log = logging.getLogger("showntell.speech")

MAX_PHONEMES = 510  # 512-token context minus the pad at each end
FRAME_SAMPLES = 600  # audio samples per duration frame
HALF_FRAMES_PER_S = 2 * SAMPLE_RATE / FRAME_SAMPLES  # timings are counted in half frames
SPACE = " "


class KokoroEngine:
    """Subclasses set `_vocab` (phoneme -> id) in `load()` and provide `_voice` and `_run`."""

    _vocab: dict[str, int]

    def __init__(self) -> None:
        self._g2ps: dict[bool, object] = {}

    def _voice(self, voice: str):
        """The voice's style vectors, indexable by phoneme count."""
        raise NotImplementedError

    def _run(self, ids: list[int], style, speed: float) -> tuple[np.ndarray, np.ndarray]:
        """Audio for one chunk, and the whole number of frames each input id (pads included) lasted."""
        raise NotImplementedError

    def _g2p(self, voice: str):
        british = voice.startswith("b")
        if british not in self._g2ps:
            from misaki import en

            self._g2ps[british] = en.G2P(trf=False, british=british, fallback=_espeak(british), unk="")
        return self._g2ps[british]

    def _encode(self, chunk: list) -> tuple[list[int], list[tuple[int, int, int | None]]]:
        """Input ids for a chunk of misaki tokens (the string KPipeline.tokens_to_ps builds),
        and per token the range of its phoneme ids and the index of the space after it."""
        ids: list[int] = []
        spans: list[tuple[int, int, int | None]] = []
        space = self._vocab[SPACE]
        for t in chunk:
            a = len(ids)
            ids += [self._vocab[c] for c in (t.phonemes or "") if c in self._vocab]
            b = len(ids)
            sp = None
            if t.whitespace and ids:  # no leading space
                sp = len(ids)
                ids.append(space)
            spans.append((a, b, sp))
        while ids and ids[-1] == space:  # no trailing space
            ids.pop()
        if len(ids) > MAX_PHONEMES:  # only a single huge token gets here: cut it, as KPipeline does
            log.warning("cutting %d phonemes to %d", len(ids), MAX_PHONEMES)
            ids = ids[:MAX_PHONEMES]
        n = len(ids)
        spans = [(min(a, n), min(b, n), sp if sp is not None and sp < n else None) for a, b, sp in spans]
        return ids, spans

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        g2p = self._g2p(voice)
        pack = self._voice(voice)
        _, mtokens = g2p(text)
        parts: list[np.ndarray] = []
        tokens: list[Token] = []
        offset = 0  # samples so far
        for chunk in chunks(mtokens):
            ids, spans = self._encode(chunk)
            if not ids:
                continue
            style = pack[min(len(ids), len(pack)) - 1]  # one style vector per chunk, by phoneme count
            audio, frames = self._run(ids, style, speed)
            tokens += word_times(chunk, spans, frames, offset / SAMPLE_RATE)
            parts.append(audio)
            offset += len(audio)
        if not parts:
            raise RuntimeError("Kokoro produced no audio")
        return np.concatenate(parts), tokens


def _espeak(british: bool):
    """espeak-ng for words misaki does not know, when the `espeak` extra (GPL) is installed."""
    try:
        from misaki import espeak

        return espeak.EspeakFallback(british=british)
    except Exception:
        return None


def word_times(chunk: list, spans: list[tuple[int, int, int | None]], frames, offset: float) -> list[Token]:
    """Start and end of each spoken word, from per-input frame counts (`frames[0]` is the pad).

    As KPipeline.join_timestamps: counted in half frames, a word runs from the middle of the
    space before it to the middle of the space after it, and the first word starts three
    frames before the pad ends. Tokens without letters or digits are left out."""
    f = [float(x) for x in frames]
    left = right = 2 * max(0.0, f[0] - 3)
    out: list[Token] = []
    for t, (a, b, sp) in zip(chunk, spans, strict=True):
        space = f[sp + 1] if sp is not None else 0.0
        if a == b:  # nothing spoken, but its space still takes time
            if sp is not None:
                left = right + space
                right = left + space
            continue
        start = left
        left = right + 2 * sum(f[a + 1 : b + 1]) + space
        right = left + space
        if any(c.isalnum() for c in t.text):
            out.append(Token(t.text, offset + start / HALF_FRAMES_PER_S, offset + left / HALF_FRAMES_PER_S))
    return out


def chunks(tokens: list) -> Iterator[list]:
    """Group misaki tokens into runs of at most MAX_PHONEMES phonemes, cut after the last sentence
    end, else clause break, else word that leaves the rest room to fit (KPipeline.en_tokenize)."""
    tks: list = []
    pcount = 0
    for t in tokens:
        next_ps = (t.phonemes or "") + (SPACE if t.whitespace else "")
        next_pcount = pcount + len(next_ps.rstrip())
        if next_pcount > MAX_PHONEMES and tks:
            z = _waterfall_last(tks, next_pcount)
            yield tks[:z]
            tks = tks[z:]
            pcount = len(_ps(tks))
            if not tks:
                next_ps = next_ps.lstrip()
        tks.append(t)
        pcount += len(next_ps)
    if tks:
        yield tks


def _ps(tokens: list) -> str:
    return "".join((t.phonemes or "") + (SPACE if t.whitespace else "") for t in tokens).strip()


def _waterfall_last(tokens: list, next_count: int) -> int:
    for marks in ("!.?…", ":;", ",—"):
        z = next((i for i in range(len(tokens) - 1, -1, -1) if tokens[i].phonemes in set(marks)), None)
        if z is None:
            continue
        z += 1
        if z < len(tokens) and tokens[z].phonemes in (")", "”"):
            z += 1
        if next_count - len(_ps(tokens[:z])) <= MAX_PHONEMES:
            return z
    return len(tokens)
