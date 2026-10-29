"""Speech backend interface and the voices: English Kokoro, Egyptian Arabic VoxCPM2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

SAMPLE_RATE = 24000

# Kokoro v1.0 English voices. The first letter is the language: a = American, b = British.
VOICES = {
    "af_heart": "American female, warm (default)",
    "af_bella": "American female, bright",
    "af_nicole": "American female, soft",
    "af_aoede": "American female",
    "af_kore": "American female",
    "af_sarah": "American female",
    "af_nova": "American female",
    "af_sky": "American female",
    "af_alloy": "American female",
    "af_jessica": "American female",
    "af_river": "American female",
    "am_michael": "American male",
    "am_fenrir": "American male",
    "am_puck": "American male",
    "am_echo": "American male",
    "am_eric": "American male",
    "am_liam": "American male",
    "am_onyx": "American male, low",
    "am_adam": "American male, deep",
    "bf_emma": "British female",
    "bf_isabella": "British female",
    "bf_alice": "British female",
    "bf_lily": "British female",
    "bm_george": "British male",
    "bm_fable": "British male",
    "bm_lewis": "British male",
    "bm_daniel": "British male",
    # Egyptian Arabic on VoxCPM2 (Apple silicon). Designed voices, not cloned from anyone: each is
    # a VoxCPM2 voice-design clip shipped in data/voices/, used as the reference for every sentence.
    "ar_eg_female": "Egyptian Arabic female, warm (Apple silicon)",
    "ar_eg_male": "Egyptian Arabic male, warm (Apple silicon)",
}
DEFAULT_VOICE = "af_heart"


def lang_of(voice: str) -> str:
    """The narration language a voice speaks: "ar" or "en"."""
    return "ar" if voice.startswith("ar_") else "en"


@dataclass
class Token:
    """A spoken token with its time span, in the order spoken."""

    text: str
    start: float
    end: float


class Backend(Protocol):
    name: str
    model_id: str  # part of the audio cache key

    def load(self) -> None:
        """Load weights and warm up. Called once, on the speech thread."""

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        """Speak `text`; return mono float32 audio at SAMPLE_RATE and timed tokens."""
