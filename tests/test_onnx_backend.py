"""The ONNX Runtime backend (Kokoro on Linux, Windows and Intel Macs).

The tests marked `onnx` load the real model and run only when the model and the spaCy model
are already cached; they never download. The timing and chunking helpers are also tested on
hand-made tokens, without the model.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from showntell.pronounce import Spoken
from showntell.speech import onnx_backend
from showntell.speech.base import SAMPLE_RATE
from showntell.speech.narrator import timed_words
from showntell.speech.onnx_backend import MAX_PHONEMES, OnnxBackend, chunks, word_times

SENTENCE = "The cache answers most requests in a few microseconds."
PARAGRAPH = (
    "Kokoro reads long narration in pieces that fit its context window. "
    "Each piece gets its own style vector, chosen by how many phonemes it holds, and the timings "
    "of later pieces are shifted by the audio that came before them. "
    "This paragraph is deliberately long, with several sentences, commas, and clauses; it needs at least two pieces. "
    "When the first piece ends, the second begins at a sentence boundary, never in the middle of a word. "
    "The words of the final sentence must still come out in order, inside the audio, with sensible times. "
    "If anything goes wrong here, the check on increasing start times will notice right away."
)


# -- without the model ------------------------------------------------------------------------


@dataclass
class Tk:
    """Stands in for misaki's MToken."""

    text: str
    phonemes: str | None
    whitespace: str = " "


def test_word_times_follow_kokoro_half_frame_rule():
    # ids: a b ␠ c .   frames: pad, a, b, space, c, ., pad
    chunk = [Tk("ab", "ab"), Tk("c", "c", ""), Tk(".", ".", "")]
    spans = [(0, 2, 2), (3, 4, None), (4, 5, None)]
    frames = [5, 2, 2, 2, 4, 3, 1]
    out = word_times(chunk, spans, frames, offset=1.0)
    assert [t.text for t in out] == ["ab", "c"]  # punctuation has no timing of its own
    # half frames: start 2*(5-3)=4; "ab" ends mid-space at 4+2*4+2=14; "c" ends at 16+2*4=24
    assert [(t.start, t.end) for t in out] == [(1 + 4 / 80, 1 + 14 / 80), (1 + 14 / 80, 1 + 24 / 80)]


def test_chunks_cut_after_a_sentence_and_fit_the_context():
    word = Tk("word", "wˈɜɹd")  # 5 phonemes + space
    sentence = [word] * 20 + [Tk("end", "ˈɛnd", ""), Tk(".", ".")]  # 126 phonemes
    tokens = sentence * 5  # 630 phonemes
    parts = list(chunks(tokens))
    assert len(parts) == 2 and sum(len(p) for p in parts) == len(tokens)
    assert parts[0][-1].text == "."
    for p in parts:
        assert len("".join((t.phonemes or "") + t.whitespace for t in p).strip()) <= MAX_PHONEMES


def test_chunks_fall_back_to_whole_words_without_punctuation():
    tokens = [Tk(f"w{i}", "wˈɜɹd") for i in range(200)]  # 1200 phonemes, no punctuation
    parts = list(chunks(tokens))
    assert len(parts) == 3 and [t for p in parts for t in p] == tokens
    assert [len(p) for p in parts] == [85, 85, 30]  # 85 words of 6 phonemes = 510


def test_unknown_variant_is_refused(tmp_path):
    with pytest.raises(ValueError, match="unknown ONNX model"):
        OnnxBackend(tmp_path, variant="model_q3")


def test_model_id_names_the_variant(tmp_path):
    assert OnnxBackend(tmp_path).model_id.endswith(onnx_backend.DEFAULT_VARIANT.replace("_", "-"))
    assert OnnxBackend(tmp_path, variant="model").model_id == "kokoro-82m-v1.0-onnx-model"


# -- with the model ---------------------------------------------------------------------------


def _data_dir() -> Path:
    from showntell.config import Config

    return Config().data_dir  # where the daemon keeps the spaCy model; only read here


def _missing() -> str | None:
    """Why the model tests cannot run here, or None."""
    from huggingface_hub import snapshot_download

    patterns = OnnxBackend(_data_dir()).patterns
    try:
        path = Path(snapshot_download(onnx_backend.REPO, allow_patterns=patterns, local_files_only=True))
    except Exception:
        return f"{onnx_backend.REPO} is not cached"
    lost = [p for p in patterns if not any(path.glob(p))]
    if lost:
        return f"{onnx_backend.REPO} is not cached ({', '.join(lost)})"
    import spacy

    from showntell.speech.assets import SPACY_NAME, spacy_dir

    if not (spacy.util.is_package(SPACY_NAME) or (spacy_dir(_data_dir()) / "config.cfg").is_file()):
        return "the spaCy English model is not downloaded (run showntell doctor)"
    return None


@pytest.fixture(scope="module")
def backend():
    why = _missing()
    if why:
        pytest.skip(why)
    from huggingface_hub import constants

    with pytest.MonkeyPatch.context() as mp:  # load() switches huggingface_hub offline; undo that afterwards
        mp.setenv("HF_HUB_OFFLINE", "1")
        mp.setattr(constants, "HF_HUB_OFFLINE", True)
        b = OnnxBackend(_data_dir())
        b.load()
        yield b


def _words(tokens):
    return [t for t in tokens if re.search(r"\w", t.text)]


@pytest.mark.onnx
def test_audio_length_is_plausible(backend):
    audio, tokens = backend.synth(SENTENCE, "af_heart", 1.0)
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32 and audio.ndim == 1
    assert np.isfinite(audio).all() and 0.05 < np.abs(audio).max() <= 1.5
    duration = len(audio) / SAMPLE_RATE
    n = len(SENTENCE.split())
    assert 0.2 * n < duration < 0.8 * n + 1, duration  # 9 words: between about 2 and 8 seconds
    assert len(audio) % 600 == 0  # whole duration frames


@pytest.mark.onnx
def test_token_times_increase_inside_the_audio(backend):
    audio, tokens = backend.synth(SENTENCE, "af_heart", 1.0)
    duration = len(audio) / SAMPLE_RATE
    assert [t.text for t in tokens] == ["The", "cache", "answers", "most", "requests", "in", "a", "few", "microseconds"]
    starts = [t.start for t in tokens]
    assert all(b > a for a, b in zip(starts, starts[1:], strict=False)), starts
    assert all(0 <= t.start < t.end <= duration for t in tokens)
    assert duration - tokens[-1].end < 0.5  # the last word ends near the end of the audio
    words = timed_words(Spoken(SENTENCE), tokens)  # the narrator finds every token in the text
    assert len(words) == 9 and words[-1]["c1"] == len(SENTENCE) - 1


@pytest.mark.onnx
@pytest.mark.slow
def test_long_paragraph_is_chunked_with_timings_offset(backend):
    _, mtokens = backend._g2p("af_heart")(PARAGRAPH)
    phonemes = sum(len(t.phonemes or "") + len(t.whitespace) for t in mtokens)
    assert phonemes > 600, phonemes
    parts = list(chunks(mtokens))
    assert len(parts) >= 2
    for p in parts[:-1]:
        assert p[-1].phonemes in (".", "!", "?", ";", ":"), p[-1].text  # cut at a sentence or clause end
    assert all(0 < len(backend._encode(p)[0]) <= MAX_PHONEMES for p in parts)

    audio, tokens = backend.synth(PARAGRAPH, "af_heart", 1.0)
    duration = len(audio) / SAMPLE_RATE
    spoken = [t.text for t in mtokens if re.search(r"\w", t.text) and t.phonemes]
    assert [t.text for t in tokens] == spoken  # every word once, in order, across the cut
    starts = [t.start for t in tokens]
    assert all(b > a for a, b in zip(starts, starts[1:], strict=False))
    assert all(0 <= t.start < t.end <= duration for t in tokens)
    first, _ = backend.synth(" ".join(t.text + t.whitespace for t in parts[0]), "af_heart", 1.0)
    second_chunk_start = next(t for t in tokens if t.start >= len(first) / SAMPLE_RATE - 0.05)
    assert second_chunk_start.text == _words(parts[1])[0].text  # later chunks are offset by earlier audio
    assert duration - tokens[-1].end < 0.5


@pytest.mark.onnx
def test_speed_scales_duration_and_timings(backend):
    slow, slow_tokens = backend.synth(SENTENCE, "af_heart", 1.0)
    fast, fast_tokens = backend.synth(SENTENCE, "af_heart", 1.5)
    ratio = len(slow) / len(fast)
    assert 1.3 < ratio < 1.7, ratio
    assert [t.text for t in fast_tokens] == [t.text for t in slow_tokens]
    assert 1.3 < slow_tokens[-1].end / fast_tokens[-1].end < 1.7


@pytest.mark.onnx
def test_inline_phonemes_are_spoken_and_keep_the_word(backend):
    plain, plain_tokens = backend.synth("Say Kokoro now.", "af_heart", 1.0)
    assert [t.text for t in plain_tokens] == ["Say", "now"]  # unknown word, no espeak fallback: skipped
    text = "Say [Kokoro](/kˈOkəɹO/) now."
    audio, tokens = backend.synth(text, "af_heart", 1.0)
    assert [t.text for t in tokens] == ["Say", "Kokoro", "now"]
    kokoro = tokens[1]
    assert kokoro.end - kokoro.start > 0.25 and len(audio) > len(plain) + 0.2 * SAMPLE_RATE
    assert timed_words(Spoken(text), tokens)[1]["c0"] == text.index("Kokoro")


@pytest.mark.onnx
def test_british_voice_and_unknown_voice(backend):
    audio, tokens = backend.synth("Hello from London.", "bf_emma", 1.0)
    assert len(audio) > SAMPLE_RATE // 2 and [t.text for t in tokens] == ["Hello", "from", "London"]
    assert set(backend._g2ps) == {False, True}
    with pytest.raises(ValueError, match="unknown voice"):
        backend.synth("Hello.", "xx_nobody", 1.0)


@pytest.mark.onnx
@pytest.mark.slow
def test_never_imports_mlx(backend):
    code = (
        "import sys; from pathlib import Path\n"
        "from showntell.speech.onnx_backend import OnnxBackend\n"
        f"b = OnnxBackend(Path({str(_data_dir())!r})); b.load(); b.synth('Hello there.', 'af_heart', 1.0)\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('mlx', 'mlx_audio')))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "HF_HUB_OFFLINE": "1"})
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().splitlines()[-1] == "[]"
