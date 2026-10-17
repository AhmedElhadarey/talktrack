"""MLX first, ONNX when MLX cannot speak.

mlx-audio is driven through a private API (see mlx_backend.py), so one of its releases can break
it. On Apple silicon the ONNX engine is installed as well; when MLX fails to load or to voice a
first sentence, speech moves to ONNX instead of failing. Audio requested before the switch is
cached under the MLX model's key; both run the same Kokoro weights.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

from .base import Backend, Token

log = logging.getLogger("showntell.speech")


class Fallback:
    def __init__(self, primary: Backend, make_secondary: Callable[[], Backend]) -> None:
        self.active = primary
        self._make_secondary = make_secondary
        self.fell_back = ""  # why the primary was given up, once it was

    @property
    def name(self) -> str:
        return self.active.name

    @property
    def model_id(self) -> str:
        return self.active.model_id

    def load(self) -> None:
        try:
            self.active.load()
            self.active.synth("Ready.", "af_heart", 1.0)
        except Exception as e:
            self.fell_back = f"{type(e).__name__}: {e}"
            log.warning("%s speech failed (%s); using ONNX instead", self.active.name, self.fell_back)
            try:
                self.active = self._make_secondary()
                self.active.load()
            except Exception as e2:
                raise RuntimeError(f"MLX failed ({self.fell_back}) and so did ONNX ({type(e2).__name__}: {e2})") from e2

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        return self.active.synth(text, voice, speed)
