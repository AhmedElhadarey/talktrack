"""Egyptian Arabic on Apple silicon: VoxCPM2 (4-bit) through mlx-audio.

The voices are designed, not cloned from anyone: each `ar_*` voice is a short VoxCPM2
voice-design clip in data/voices/, passed as the reference for every sentence so the speaker
stays the same from slide to slide. Narration is voiced one sentence at a time (the model
drifts on long input, and short calls keep the memory peak down), and word timings come from
the audio (align.py), because the model reports none.

The model is 2.3 GB on disk and peaks near 4.7 GB while speaking, so it loads only when an
Arabic voice is first asked for, refuses to load when less than 4 GB is free, and is
dropped again after IDLE_S without Arabic narration. While it is loaded, MLX may hold up to
6 GB (resources.ARABIC_SPEECH_MAX) instead of the English voice's share.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import re
import sys
import threading
from pathlib import Path

import numpy as np

from ..resources import GB, budget
from .align import sentence_tokens
from .assets import Status, hf_snapshot
from .base import SAMPLE_RATE, Token

log = logging.getLogger("talktrack.speech")

REPO = "mlx-community/VoxCPM2-4bit"
PATTERNS = ["config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]
VOICE_DIR = Path(__file__).parent.parent / "data" / "voices"
PEAK_BYTES = int(4.7 * GB)  # measured: 4.66 GB on the first sentence, 4-bit, mlx-audio 0.5.9
# Loading is refused below this much free memory. Less than the peak: macOS compresses idle
# memory, and the spike ran from 4.5 GB free with the level falling to 20 % and no swapping.
LOAD_MIN_FREE = 4 * GB
IDLE_S = 600.0
SENTENCE_GAP_S = 0.2  # silence between the sentences of one paragraph
_SENTENCES = re.compile(r"(?<=[.!?؟…])\s+|\n+")


def available() -> bool:
    return sys.platform == "darwin" and importlib.util.find_spec("mlx_audio") is not None


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCES.split(text) if s.strip()]


def check() -> tuple[str, str]:
    """For `talktrack doctor`, without loading the model: ("ok" | "warn", what to say)."""
    if not available():
        return "warn", "not here: Arabic voices need a Mac with Apple silicon"
    try:
        from huggingface_hub import snapshot_download

        Path(snapshot_download(REPO, allow_patterns=PATTERNS, local_files_only=True))
        cached = "model downloaded"
    except Exception:
        cached = "model (2.3 GB) downloads on first Arabic narration"
    free = budget().machine.available
    if free is not None and free < LOAD_MIN_FREE:
        return "warn", f"ar_eg_female, ar_eg_male; {cached}; only {free / GB:.1f} GB free, it needs {LOAD_MIN_FREE / GB:.0f} GB to load"
    return "ok", f"ar_eg_female, ar_eg_male; {cached}"


class VoxBackend:
    name = "voxcpm2"
    model_id = "voxcpm2-4bit-mlx"

    def __init__(self, status: Status | None = None, idle_s: float = IDLE_S) -> None:
        self.status = status
        self.idle_s = idle_s
        self._model = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def load(self) -> None:
        """Nothing up front: the model loads with the first Arabic sentence."""

    def _ensure(self):
        if self._model is not None:
            return self._model
        free = budget().machine.available
        if free is not None and free < LOAD_MIN_FREE:
            raise RuntimeError(
                f"the Arabic voice needs at least {LOAD_MIN_FREE / GB:.0f} GB of free memory (it peaks near "
                f"{PEAK_BYTES / GB:.1f} GB) and {free / GB:.1f} GB is free; close some apps and try again"
            )
        try:
            path = hf_snapshot(REPO, PATTERNS, self.status, "the Arabic voice model (2.3 GB)", online=True)
            if self.status:
                self.status.set("loading", "the Arabic voice model")
            from mlx_audio.tts.utils import load_model

            from .mlx_backend import _limit_memory

            self._model = load_model(path)
            _limit_memory(budget().arabic_speech_limit())
        finally:
            if self.status:  # English speech was ready all along; a failure is reported per job
                self.status.set("ready")
        log.info("Arabic voice model loaded")
        return self._model

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        """`speed` is not used: VoxCPM2 has no speed control."""
        clip = VOICE_DIR / f"{voice}.flac"
        if not clip.is_file():
            raise ValueError(f"unknown voice {voice!r}")
        with self._lock:
            if self._timer:
                self._timer.cancel()
            try:
                return self._speak(text, str(clip))
            finally:
                self._timer = threading.Timer(self.idle_s, self.unload)
                self._timer.daemon = True
                self._timer.start()

    def _speak(self, text: str, clip: str) -> tuple[np.ndarray, list[Token]]:
        import mlx.core as mx
        from mlx_audio.utils import resample_audio

        model = self._ensure()
        gap = np.zeros(int(SENTENCE_GAP_S * SAMPLE_RATE), dtype=np.float32)
        parts: list[np.ndarray] = []
        tokens: list[Token] = []
        t = 0.0
        for i, sent in enumerate(sentences(text)):
            # the same sentence always sounds the same, so the audio cache stays truthful
            mx.random.seed(int(hashlib.sha256(sent.encode()).hexdigest()[:8], 16))
            chunks = [np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in model.generate(sent, ref_audio=clip, verbose=False)]
            mx.clear_cache()  # see mlx_backend._run: kept buffers grow until the Mac swaps
            audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
            if model.sample_rate != SAMPLE_RATE:
                audio = np.asarray(resample_audio(audio, model.sample_rate, SAMPLE_RATE), dtype=np.float32)
            if i:
                parts.append(gap)
                t += SENTENCE_GAP_S
            tokens += sentence_tokens(sent, audio, t)
            parts.append(audio)
            t += len(audio) / SAMPLE_RATE
        audio = np.concatenate(parts) if parts else np.zeros(int(0.1 * SAMPLE_RATE), dtype=np.float32)
        return audio, tokens

    def unload(self) -> None:
        """Give the memory back; the next Arabic sentence loads the model again."""
        with self._lock:
            if self._model is None:
                return
            self._model = None
            try:
                import gc

                import mlx.core as mx

                gc.collect()
                mx.clear_cache()
                from .mlx_backend import _limit_memory

                _limit_memory()  # back to the English voice's share
            except Exception:
                pass
            log.info("Arabic voice model unloaded after %.0f s idle", self.idle_s)
