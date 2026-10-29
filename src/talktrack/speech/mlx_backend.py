"""Kokoro on Apple silicon through mlx-audio.

Only two mlx-audio calls are used: `load_model` and the model's forward pass with
`return_output=True`, which returns the audio and each input's length in frames. Phonemes,
chunks, voices and word timings are talktrack's own, shared with the ONNX engine (kokoro.py),
so mlx-audio's text pipeline and its espeak import are never touched.
tests/test_mlx_adapter.py fails loudly when an mlx-audio release changes either call.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import numpy as np

from ..resources import budget
from .assets import Status, ensure_spacy, go_offline, hf_snapshot
from .kokoro import KokoroEngine

REPO = "mlx-community/Kokoro-82M-bf16"
# Held around every MLX call in speech: the Arabic voice frees its memory from a timer thread
# (voxcpm.py), and that must not run while Kokoro is inside MLX on the speech thread.
MLX_LOCK = threading.RLock()
PATTERNS = ["config.json", "kokoro-v1_0.safetensors", "voices/a*.safetensors", "voices/b*.safetensors"]


def available() -> bool:
    return sys.platform == "darwin" and importlib.util.find_spec("mlx_audio") is not None


class MlxBackend(KokoroEngine):
    name = "mlx"
    model_id = "kokoro-82m-v1.0-mlx-bf16"

    def __init__(self, data_dir: Path, status: Status | None = None) -> None:
        super().__init__()
        self.data_dir = data_dir
        self.status = status
        self._voices: dict[str, object] = {}

    def load(self) -> None:
        self.path = hf_snapshot(REPO, PATTERNS, self.status, "the Kokoro voice model (330 MB)")
        ensure_spacy(self.data_dir, self.status)
        go_offline()
        if self.status:
            self.status.set("loading")
        from mlx_audio.tts.utils import load_model

        self._vocab: dict[str, int] = json.loads((self.path / "config.json").read_text(encoding="utf-8"))["vocab"]
        self._phoneme = {i: c for c, i in self._vocab.items()}
        self._model = load_model(self.path)
        _limit_memory()

    def _voice(self, voice: str):
        if voice not in self._voices:
            f = self.path / "voices" / f"{voice}.safetensors"
            if not f.is_file():
                raise ValueError(f"unknown voice {voice!r}")
            import mlx.core as mx

            self._voices[voice] = mx.load(str(f))["voice"]  # (510, 1, 256)
        return self._voices[voice]

    def _run(self, ids: list[int], style, speed: float) -> tuple[np.ndarray, np.ndarray]:
        # The model takes phonemes as text and looks each one up in the same vocabulary.
        import mlx.core as mx

        with MLX_LOCK:
            out = self._model("".join(self._phoneme[i] for i in ids), style, speed, return_output=True)
            frames = np.asarray(out.pred_dur).reshape(-1)  # whole frames, pads included
            audio = np.asarray(out.audio, dtype=np.float32).reshape(-1)
            del out
            # MLX keeps freed GPU buffers for reuse, and chunks of every length leave buffers of every
            # size: kept, they grow to most of the Mac's memory (25 GB of 32 after ten sentences) and
            # the machine swaps. Copied out to numpy, nothing here is needed again.
            mx.clear_cache()
        if len(frames) != len(ids) + 2:
            raise RuntimeError(f"mlx-audio returned {len(frames)} durations for {len(ids)} phonemes")
        return audio, frames


def _limit_memory(limit: int | None = None) -> None:
    """Cap MLX at `limit` bytes, by default the speech share of the memory budget. The call moved
    from mx.metal in newer mlx; whichever is missing or fails, speech goes on without a limit."""
    if limit is None:
        limit = budget().speech_limit()
    if limit is None:
        return
    try:
        import mlx.core as mx

        setter = getattr(mx, "set_memory_limit", None) or mx.metal.set_memory_limit
        setter(limit)
    except Exception:
        pass
