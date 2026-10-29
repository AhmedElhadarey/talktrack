"""One backend for every voice: English voices to Kokoro, Arabic ones (`ar_*`) to VoxCPM2.

Kokoro loads at start as before. The Arabic model loads on its first sentence (voxcpm.py), so
a machine that never narrates in Arabic never downloads or holds it.
"""

from __future__ import annotations

import numpy as np

from .base import Backend, Token, lang_of


class Voices:
    def __init__(self, english: Backend, arabic: Backend | None, no_arabic: str = "") -> None:
        self.english = english
        self.arabic = arabic
        self.no_arabic = no_arabic  # why Arabic voices cannot speak here, when they cannot

    @property
    def name(self) -> str:
        return self.english.name

    @property
    def model_id(self) -> str:
        return self.english.model_id

    @property
    def fell_back(self) -> str:
        return getattr(self.english, "fell_back", "")

    def model_for(self, voice: str) -> str:
        """The model a voice speaks with: part of its audio cache key."""
        if lang_of(voice) == "ar" and self.arabic is not None:
            return self.arabic.model_id
        return self.english.model_id

    def unavailable(self, voice: str) -> str:
        """Why `voice` cannot speak on this machine, or "" when it can."""
        if lang_of(voice) == "ar" and self.arabic is None:
            return self.no_arabic or "Arabic voices are not available here"
        return ""

    def load(self) -> None:
        self.english.load()

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        if lang_of(voice) != "ar":
            return self.english.synth(text, voice, speed)
        if self.arabic is None:
            raise RuntimeError(self.unavailable(voice))
        return self.arabic.synth(text, voice, speed)
