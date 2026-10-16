"""Kokoro on Apple silicon through mlx-audio.

mlx-audio has no public API for per-token timings, so this adapter calls the model's
private `_get_pipeline`. Everything that depends on mlx-audio internals is in this file;
tests/test_mlx_adapter.py fails loudly when an mlx-audio release breaks it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import sys
import types
from pathlib import Path

import numpy as np

from .assets import Status, ensure_spacy, go_offline, hf_snapshot
from .base import Token

REPO = "mlx-community/Kokoro-82M-bf16"
PATTERNS = ["config.json", "kokoro-v1_0.safetensors", "voices/a*.safetensors", "voices/b*.safetensors"]


def available() -> bool:
    return sys.platform == "darwin" and importlib.util.find_spec("mlx_audio") is not None


class MlxBackend:
    name = "mlx"
    model_id = "kokoro-82m-v1.0-mlx-bf16"

    def __init__(self, data_dir: Path, status: Status | None = None) -> None:
        self.data_dir = data_dir
        self.status = status
        self._pipelines: dict[str, object] = {}

    def load(self) -> None:
        path = hf_snapshot(REPO, PATTERNS, self.status, "the Kokoro voice model (330 MB)")
        ensure_spacy(self.data_dir, self.status)
        go_offline()
        if self.status:
            self.status.set("loading")
        _stub_espeak_if_missing()
        logging.getLogger().addFilter(_quiet_espeak_warning)
        from mlx_audio.tts.utils import load_model

        self._model = load_model(path)
        self._model.repo_id = REPO

    def _pipeline(self, voice: str):
        lang = voice[0] if voice[:1] in ("a", "b") else "a"
        if lang not in self._pipelines:
            with contextlib.redirect_stdout(sys.stderr):  # mlx-audio prints progress to stdout
                self._pipelines[lang] = self._model._get_pipeline(lang)  # private: see module docstring
        return self._pipelines[lang]

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        pipe = self._pipeline(voice)
        parts: list[np.ndarray] = []
        tokens: list[Token] = []
        offset = 0.0
        for r in pipe(text, voice=voice, speed=speed, split_pattern=None):
            if r.audio is None:
                continue
            a = np.array(r.audio, dtype=np.float32).reshape(-1)
            for tk in r.tokens or []:
                if tk.start_ts is None or tk.end_ts is None:
                    continue
                tokens.append(Token(tk.text, offset + tk.start_ts, offset + tk.end_ts))
            parts.append(a)
            offset += len(a) / 24000
        if not parts:
            raise RuntimeError("Kokoro produced no audio")
        return np.concatenate(parts), tokens


def _stub_espeak_if_missing() -> None:
    """mlx-audio imports misaki.espeak outside its own error handling. Without the optional
    espeak packages (GPL), install a stand-in whose fallback refuses to start, which
    mlx-audio handles by running without a fallback."""
    if "misaki.espeak" in sys.modules:
        return
    try:
        import misaki.espeak  # noqa: F401  # installed with the showntell[espeak] extra

        return
    except Exception:
        pass
    stub = types.ModuleType("misaki.espeak")

    class EspeakFallback:  # noqa: D401
        def __init__(self, *a, **k):
            raise RuntimeError("espeak-ng is not installed")

    stub.EspeakFallback = EspeakFallback
    sys.modules["misaki.espeak"] = stub


def _quiet_espeak_warning(record: logging.LogRecord) -> bool:
    """mlx-audio warns that unknown words will be skipped without espeak; showntell's
    pronunciation layer gives every word a pronunciation first, so the warning misleads."""
    msg = str(record.getMessage())
    return not ("EspeakFallback" in msg or "espeak-ng is not installed" in msg)
