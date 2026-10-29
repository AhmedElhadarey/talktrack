"""Speech: backends (MLX, ONNX, fake), the worker queue and the audio cache."""

from __future__ import annotations

from pathlib import Path

from .assets import Status
from .base import DEFAULT_VOICE, SAMPLE_RATE, VOICES, Backend
from .narrator import Narrator

NO_ARABIC = "Arabic voices need a Mac with Apple silicon (they run VoxCPM2 through MLX)"


def make_backend(kind: str, data_dir: Path, status: Status) -> Backend:
    """The backend for every voice: `kind` picks the English engine, and Arabic voices go to
    VoxCPM2 on Apple silicon (or to the fake engine when `kind` is fake)."""
    from . import voxcpm
    from .router import Voices

    english = _english(kind, data_dir, status)
    if kind == "fake":
        return Voices(english, english)
    if voxcpm.available():
        return Voices(english, voxcpm.VoxBackend(status))
    return Voices(english, None, NO_ARABIC)


def _english(kind: str, data_dir: Path, status: Status) -> Backend:
    from . import mlx_backend

    if kind == "auto" and mlx_backend.available():
        from .fallback import Fallback

        return Fallback(mlx_backend.MlxBackend(data_dir, status), lambda: _english("onnx", data_dir, status))
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
