"""A deterministic stand-in for tests and machines without a speech model."""

from __future__ import annotations

import re

import numpy as np

from .base import SAMPLE_RATE, Token


class FakeBackend:
    name = "fake"
    model_id = "fake-1"

    def __init__(self, word_seconds: float = 0.25, delay: float = 0.0) -> None:
        self.word_seconds = word_seconds
        self.delay = delay

    def load(self) -> None:
        pass

    def synth(self, text: str, voice: str, speed: float) -> tuple[np.ndarray, list[Token]]:
        if self.delay:
            import time

            time.sleep(self.delay)
        step = self.word_seconds / max(0.25, speed)
        tokens, t = [], 0.05
        for m in re.finditer(r"\S+", text):
            word = m.group(0)
            tokens.append(Token(word, t, t + step * 0.85))
            t += step
        n = int((t + 0.1) * SAMPLE_RATE)
        audio = (0.02 * np.sin(np.arange(n) * 2 * np.pi * 220 / SAMPLE_RATE)).astype(np.float32)
        return audio, tokens
