"""Early warning for mlx-audio upgrades.

The MLX adapter calls mlx-audio's private `_get_pipeline` to get per-token timings. These tests
load the real Kokoro model and fail loudly when an mlx-audio release moves or changes that API.
They run only on Apple silicon with `mlx-community/Kokoro-82M-bf16` and the spaCy model cached.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import numpy as np
import pytest

from showntell.speech.base import SAMPLE_RATE

pytestmark = pytest.mark.mlx

REPO = "mlx-community/Kokoro-82M-bf16"
SENTENCE = "The cache answers most requests in a few microseconds."


def _missing() -> str | None:
    """Why these tests cannot run here, or None. Checked lazily so collection stays fast."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        return "needs macOS on Apple silicon"
    from showntell.speech import mlx_backend

    if not mlx_backend.available():
        return "mlx-audio is not installed"
    from huggingface_hub import try_to_load_from_cache

    for name in ("config.json", "kokoro-v1_0.safetensors", "voices/af_heart.safetensors", "voices/bf_emma.safetensors"):
        if not isinstance(try_to_load_from_cache(REPO, name), str):
            return f"{REPO} is not cached ({name})"
    import spacy

    from showntell.speech.assets import SPACY_NAME, spacy_dir

    if not (spacy.util.is_package(SPACY_NAME) or (spacy_dir(_data_dir()) / "config.cfg").is_file()):
        return "the spaCy English model is not downloaded (run showntell doctor)"
    return None


def _data_dir() -> Path:
    from showntell.config import Config

    return Config().data_dir  # where the daemon keeps the spaCy model; only read here


@pytest.fixture(scope="module")
def backend():
    why = _missing()
    if why:
        pytest.skip(why)
    from huggingface_hub import constants

    from showntell.speech.mlx_backend import MlxBackend

    with pytest.MonkeyPatch.context() as mp:  # load() switches huggingface_hub offline; undo that afterwards
        mp.setenv("HF_HUB_OFFLINE", "1")
        mp.setattr(constants, "HF_HUB_OFFLINE", True)
        b = MlxBackend(_data_dir())
        b.load()
        yield b


def test_private_get_pipeline_still_exists(backend):
    assert callable(getattr(backend._model, "_get_pipeline", None)), "mlx-audio removed Model._get_pipeline"


def test_pipeline_results_still_carry_audio_and_timed_tokens(backend):
    pipe = backend._pipeline("af_heart")
    results = list(pipe("Hello there.", voice="af_heart", speed=1.0, split_pattern=None))
    assert results, "the pipeline yielded nothing"
    r = results[0]
    assert hasattr(r, "audio") and hasattr(r, "tokens"), f"pipeline result changed shape: {type(r)}"
    assert r.tokens, "the pipeline no longer returns tokens"
    tk = r.tokens[0]
    for attr in ("text", "start_ts", "end_ts"):
        assert hasattr(tk, attr), f"token lost `{attr}`: {type(tk)}"


def test_synth_returns_audio_and_tokens_with_increasing_start_times(backend):
    audio, tokens = backend.synth(SENTENCE, "af_heart", 1.0)
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32 and audio.ndim == 1
    duration = len(audio) / SAMPLE_RATE
    assert 1.0 < duration < 15.0 and np.abs(audio).max() > 0.01
    words = [t for t in tokens if any(c.isalnum() for c in t.text)]
    assert len(words) >= 8, [t.text for t in tokens]
    starts = [t.start for t in words]
    assert all(b > a for a, b in zip(starts, starts[1:], strict=False)), starts
    assert all(t.end >= t.start for t in tokens) and tokens[-1].end <= duration + 0.1
    assert words[0].text.lower() == "the"


def test_british_voice_uses_its_own_pipeline(backend):
    audio, tokens = backend.synth("Hello from London.", "bf_emma", 1.0)
    assert len(audio) > SAMPLE_RATE // 2 and tokens
    assert set(backend._pipelines) >= {"a", "b"}
