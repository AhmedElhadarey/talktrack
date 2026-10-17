"""Kokoro on Apple silicon through mlx-audio.

Only two mlx-audio calls are used: `load_model` and the model's forward pass with
`return_output=True`, which returns the audio and each input's length in frames. Phonemes,
chunks, voices and word timings are showntell's own, shared with the ONNX engine (kokoro.py),
so mlx-audio's text pipeline and its espeak import are never touched.
tests/test_mlx_adapter.py fails loudly when an mlx-audio release changes either call.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from .assets import Status, ensure_spacy, go_offline, hf_snapshot
from .kokoro import KokoroEngine

REPO = "mlx-community/Kokoro-82M-bf16"
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
        out = self._model("".join(self._phoneme[i] for i in ids), style, speed, return_output=True)
        frames = np.asarray(out.pred_dur).reshape(-1)  # whole frames, pads included
        if len(frames) != len(ids) + 2:
            raise RuntimeError(f"mlx-audio returned {len(frames)} durations for {len(ids)} phonemes")
        return np.asarray(out.audio, dtype=np.float32).reshape(-1), frames
