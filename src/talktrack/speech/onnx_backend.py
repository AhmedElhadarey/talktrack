"""Kokoro on ONNX Runtime, for Linux, Windows and Intel Macs.

The model is onnx-community/Kokoro-82M-v1.0-ONNX-timestamped: Kokoro-82M v1.0 exported with
a second output, `durations`, the predicted length of each input phoneme in frames of 600
samples (40 per second at 24 kHz) before the model rounds them to whole frames.
Phonemes, chunks and word timings are shared with the MLX engine (kokoro.py).

Default weights: `model_quantized` (int8, 92 MB). On an M1 Max CPU it ran at 0.10 s of
compute per second of audio against 0.17-0.22 for fp32, with the same whisper word error
rate (0 on 30 test clips). The fp16 variants voice some inputs as silence on CPU.
TALKTRACK_ONNX_MODEL picks another variant (e.g. `model` for fp32); TALKTRACK_THREADS sets
the thread count.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

from ..resources import budget
from .assets import Status, ensure_spacy, go_offline, hf_snapshot
from .kokoro import KokoroEngine

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
STYLE_DIM = 256
MAX_THREADS = 6  # more threads stop paying off (measured on 8 performance cores)


class OnnxBackend(KokoroEngine):
    name = "onnx"

    def __init__(self, data_dir: Path, status: Status | None = None, variant: str | None = None) -> None:
        super().__init__()
        self.data_dir = data_dir
        self.status = status
        self.variant = variant or os.environ.get("TALKTRACK_ONNX_MODEL") or DEFAULT_VARIANT
        if self.variant not in VARIANTS:
            raise ValueError(f"unknown ONNX model {self.variant!r}; use one of {', '.join(VARIANTS)}")
        self.model_id = f"kokoro-82m-v1.0-onnx-{self.variant.replace('_', '-')}"
        self.model_file = f"onnx/{self.variant}.onnx"
        self.patterns = [self.model_file, "tokenizer.json", *VOICE_PATTERNS]
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

    def _voice(self, voice: str) -> np.ndarray:
        if voice not in self._voices:
            f = self.path / "voices" / f"{voice}.bin"
            if not f.is_file():
                raise ValueError(f"unknown voice {voice!r}")
            self._voices[voice] = np.fromfile(f, dtype=np.float32).reshape(-1, 1, STYLE_DIM)
        return self._voices[voice]

    def _run(self, ids: list[int], style, speed: float) -> tuple[np.ndarray, np.ndarray]:
        wave, durations = self._session.run(
            None,
            {
                "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
                "style": style,
                "speed": np.array([speed], dtype=np.float32),
            },
        )
        frames = np.maximum(1, np.round(np.asarray(durations).reshape(-1)))  # the model's own rounding
        return np.asarray(wave, dtype=np.float32).reshape(-1), frames


def _threads() -> int:
    env = os.environ.get("TALKTRACK_THREADS", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    return budget().speech_threads(MAX_THREADS)  # CPUs and RAM from resources.py
