"""Early warning for mlx-audio upgrades.

The MLX adapter uses two mlx-audio calls: `load_model` and the model's forward pass with
`return_output=True`. These tests load the real Kokoro model and fail loudly when an mlx-audio
release changes either. They run only on Apple silicon with `mlx-community/Kokoro-82M-bf16` and
the spaCy model cached.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import numpy as np
import pytest

from showntell.speech.base import SAMPLE_RATE
from showntell.speech.kokoro import FRAME_SAMPLES

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


def test_forward_pass_returns_audio_and_a_duration_per_input(backend):
    phonemes = "hə lˈO"
    ids = [backend._vocab[c] for c in phonemes]
    out = backend._model(phonemes, backend._voice("af_heart")[len(ids) - 1], 1.0, return_output=True)
    frames = np.asarray(out.pred_dur).reshape(-1)
    assert len(frames) == len(ids) + 2 and frames.min() >= 1  # one per phoneme, plus the pads
    assert np.asarray(out.audio).size == frames.sum() * FRAME_SAMPLES  # word timings count in these frames


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
    assert len(audio) > SAMPLE_RATE // 2 and [t.text for t in tokens] == ["Hello", "from", "London"]
    assert set(backend._g2ps) == {False, True}
    with pytest.raises(ValueError, match="unknown voice"):
        backend.synth("Hello.", "xx_nobody", 1.0)


def test_a_word_misaki_cannot_say_does_not_shift_the_rest(backend):
    _, tokens = backend.synth("Say zxqvbn now, then stop.", "af_heart", 1.0)
    assert [t.text for t in tokens] == ["Say", "now", "then", "stop"]  # the unknown word is skipped
    starts = [t.start for t in tokens]
    assert all(b > a for a, b in zip(starts, starts[1:], strict=False)), starts


def test_freed_gpu_buffers_are_given_back_after_each_chunk(backend):
    import mlx.core as mx

    for n in (3, 11, 27, 40):  # chunks of different lengths leave buffers of different sizes
        backend.synth(" ".join(["the token is checked against the session"] * (n // 7 + 1)), "af_heart", 1.0)
    assert mx.get_cache_memory() < 200_000_000
