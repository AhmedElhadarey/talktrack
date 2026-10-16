"""Kokoro on ONNX Runtime, for Linux, Windows and Intel Macs.

The model is onnx-community/Kokoro-82M-v1.0-ONNX-timestamped: Kokoro-82M v1.0 exported with
a second output, `durations`, the predicted length of each input phoneme in frames of 600
samples (40 per second at 24 kHz) before the model rounds them to whole frames.
Phonemes come from misaki's English G2P without the espeak fallback. Chunking, the style
vector per chunk and word timings follow Kokoro's own pipeline (`kokoro.KPipeline`, which
mlx-audio copies), so both backends time words the same way.

Default weights: `model_quantized` (int8, 92 MB). On an M1 Max CPU it ran at 0.10 s of
compute per second of audio against 0.17-0.22 for fp32, with the same whisper word error
rate (0 on 30 test clips). The fp16 variants voice some inputs as silence on CPU.
SHOWNTELL_ONNX_MODEL picks another variant (e.g. `model` for fp32); SHOWNTELL_THREADS sets
the thread count.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from pathlib import Path

import numpy as np

from .assets import Status, ensure_spacy, go_offline, hf_snapshot
from .base import SAMPLE_RATE, Token

log = logging.getLogger("showntell.speech")

REPO = "onnx-community/Kokoro-82M-v1.0-ONNX-timestamped"
# Weight variants in the repo (onnx/<name>.onnx) and their download size in MB.
VARIANTS = {
    "model": 326,  # fp32
    "model_fp16": 163,
    "model_quantized": 92,  # int8 weights and activations
    "model_q8f16": 86,
    "model_uint8": 177,
    "model_uint8f16": 114,
    "model_q4": 305,
    "model_q4f16": 155,
}
DEFAULT_VARIANT = "model_quantized"
VOICE_PATTERNS = ["voices/a*.bin", "voices/b*.bin"]  # the English voices, 15 MB
MAX_PHONEMES = 510  # 512-token context minus the pad at each end
STYLE_DIM = 256
FRAME_SAMPLES = 600  # audio samples per duration frame
HALF_FRAMES_PER_S = 2 * SAMPLE_RATE / FRAME_SAMPLES  # timings are counted in half frames
MAX_THREADS = 6  # more threads stop paying off (measured on 8 performance cores)
SPACE = " "


class OnnxBackend:
    name = "onnx"

    def __init__(self, data_dir: Path, status: Status | None = None, variant: str | None = None) -> None:
        self.data_dir = data_dir
        self.status = status
        self.variant = variant or os.environ.get("SHOWNTELL_ONNX_MODEL") or DEFAULT_VARIANT
        if self.variant not in VARIANTS:
            raise ValueError(f"unknown ONNX model {self.variant!r}; use one of {', '.join(VARIANTS)}")
        self.model_id = f"kokoro-82m-v1.0-onnx-{self.variant.replace('_', '-')}"
        self.model_file = f"onnx/{self.variant}.onnx"
        self.patterns = [self.model_file, "tokenizer.json", *VOICE_PATTERNS]
        self._g2ps: dict[bool, object] = {}
        self._voices: dict[str, np.ndarray] = {}

    def load(self) -> None:
        self.path = self._snapshot(f"the Kokoro voice model ({VARIANTS[self.variant] + 15} MB)")
        ensure_spacy(self.data_dir, self.status)
        go_offline()
        if self.status:
            self.status.set("loading")
        import onnxruntime as ort

        tokenizer = json.loads((self.path / "tokenizer.json").read_text(encoding="utf-8"))
        self._vocab: dict[str, int] = tokenizer["model"]["vocab"]  # phoneme -> id; "$" (0) is the pad
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = _threads()
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.log_severity_level = 3  # errors only
        self._session = ort.InferenceSession(str(self.path / self.model_file), opts, providers=["CPUExecutionProvider"])

    def _snapshot(self, label: str) -> Path:
        return hf_snapshot(REPO, self.patterns, self.status, label)

    # -- text to phonemes ---------------------------------------------------------------

    def _g2p(self, voice: str):
        british = voice.startswith("b")
        if british not in self._g2ps:
            from misaki import en

            self._g2ps[british] = en.G2P(trf=False, british=british, fallback=None, unk="")
        return self._g2ps[british]

    def _voice(self, voice: str) -> np.ndarray:
        if voice not in self._voices:
            f = self.path / "voices" / f"{voice}.bin"
            if not f.is_file():
                raise ValueError(f"unknown voice {voice!r}")
            self._voices[voice] = np.fromfile(f, dtype=np.float32).reshape(-1, 1, STYLE_DIM)
        return self._voices[voice]

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

    # -- synthesis ----------------------------------------------------------------------

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
            wave, durations = self._session.run(None, {
                "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
                "style": style,
                "speed": np.array([speed], dtype=np.float32),
            })
            audio = np.asarray(wave, dtype=np.float32).reshape(-1)
            frames = np.maximum(1, np.round(np.asarray(durations).reshape(-1)))  # the model's own rounding
            tokens += word_times(chunk, spans, frames, offset / SAMPLE_RATE)
            parts.append(audio)
            offset += len(audio)
        if not parts:
            raise RuntimeError("Kokoro produced no audio")
        return np.concatenate(parts), tokens


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


def _threads() -> int:
    env = os.environ.get("SHOWNTELL_THREADS", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    try:
        cpus = len(os.sched_getaffinity(0))  # respects container and taskset limits
    except AttributeError:  # macOS, Windows
        cpus = os.cpu_count() or 4
    return max(1, min(MAX_THREADS, cpus))
