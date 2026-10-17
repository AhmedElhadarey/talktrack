"""Speech: backends (MLX, ONNX, fake), the worker queue and the audio cache."""

from __future__ import annotations

from pathlib import Path

from .assets import Status
from .base import DEFAULT_VOICE, SAMPLE_RATE, VOICES, Backend
from .narrator import Narrator


def make_backend(kind: str, data_dir: Path, status: Status) -> Backend:
    from . import mlx_backend

    if kind == "auto" and mlx_backend.available():
        from .fallback import Fallback

        return Fallback(mlx_backend.MlxBackend(data_dir, status), lambda: make_backend("onnx", data_dir, status))
    if kind == "auto":
        kind = "onnx"
    if kind == "mlx":
        return mlx_backend.MlxBackend(data_dir, status)
    if kind == "onnx":
        from .onnx_backend import OnnxBackend

        return OnnxBackend(data_dir, status)
    if kind == "fake":
        from .fake import FakeBackend

        return FakeBackend()
    raise ValueError(f"unknown speech backend {kind!r}; use auto, mlx, onnx or fake")


__all__ = ["DEFAULT_VOICE", "SAMPLE_RATE", "VOICES", "Backend", "Narrator", "Status", "make_backend"]
