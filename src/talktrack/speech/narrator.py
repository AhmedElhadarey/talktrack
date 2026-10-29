"""The speech worker: one thread, a priority queue, and a content-addressed audio cache.

Jobs are ordered by their position inside their walkthrough, so the opening narration of a
new walkthrough jumps ahead of a backlog from another one. Audio files are named by a hash
of model, voice, speed and spoken text; a repeat request is answered from disk.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from ..pronounce import Spoken, speakable
from .assets import Status
from .base import SAMPLE_RATE, Backend, Token, lang_of

log = logging.getLogger("talktrack.speech")

Done = Callable[[dict], None]
RELOAD_AFTER_S = 30.0  # after a failed load, the next narration tries again once this long has passed


@dataclass
class _Job:
    key: str
    spoken: Spoken
    voice: str
    speed: float


class Narrator:
    def __init__(self, backend: Backend, cache_dir: Path, status: Status | None = None) -> None:
        self.backend = backend
        self.cache_dir = cache_dir
        self.status = status or Status()
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._waiting: dict[str, list[Done]] = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self.load_error: str | None = None
        self._load_at = 0.0  # when loading was last tried
        self.stats = {"jobs": 0, "audio_s": 0.0, "compute_s": 0.0, "cache_hits": 0}
        self.lexicon: dict[str, str] = {}
        self._thread = threading.Thread(target=self._run, name="speech", daemon=True)

    # -- public -------------------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self.load_error is None

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready.wait(timeout)

    def backlog(self) -> int:
        return self._q.qsize()

    def speed_factor(self) -> float | None:
        """Seconds of audio produced per second of compute."""
        if not self.stats["compute_s"]:
            return None
        return self.stats["audio_s"] / self.stats["compute_s"]

    def prepare(self, display: str, code=(), say=(), voice: str = "af_heart", speed: float = 1.0) -> tuple[str, Spoken]:
        spoken = speakable(display, code, say, self.lexicon, british=voice.startswith("b"), lang=lang_of(voice))
        model_for = getattr(self.backend, "model_for", None)
        model = model_for(voice) if model_for else self.backend.model_id
        key = hashlib.sha256(f"{model}|{voice}|{speed:.2f}|{spoken.text}".encode()).hexdigest()[:24]
        return key, spoken

    def cached(self, key: str) -> dict | None:
        meta = self.cache_dir / f"{key}.json"
        if meta.is_file() and (self.cache_dir / f"{key}.wav").is_file():
            try:
                return json.loads(meta.read_text())
            except json.JSONDecodeError:
                return None
        return None

    def request(self, key: str, spoken: Spoken, voice: str, speed: float, priority: int, done: Done) -> None:
        """Voice `spoken` unless cached; `done(result)` runs on the speech thread or immediately."""
        hit = self.cached(key)
        if hit is not None:
            self.stats["cache_hits"] += 1
            done(hit)
            return
        with self._lock:
            if key in self._waiting:
                self._waiting[key].append(done)
                return
            self._waiting[key] = [done]
        self._q.put((priority, next(self._seq), _Job(key, spoken, voice, speed)))

    def audio_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.wav"

    # -- worker -------------------------------------------------------------------------

    def _load(self) -> None:
        t0 = time.time()
        self._load_at = t0
        try:
            from .. import pronounce

            pronounce.warm()  # misaki's dictionary, so the first narration does not pay for it
            self.status.set("loading")
            self.backend.load()
            self.backend.synth("Ready.", "af_heart", 1.0)  # warms phonemes, spaCy and a voice
            self.load_error = None
            self.status.set("ready")
            log.info("speech ready (%s) in %.1fs", self.backend.name, time.time() - t0)
        except Exception as e:  # surfaced to the agent, /health and doctor
            log.exception("speech backend failed to load")
            self.load_error = f"{type(e).__name__}: {e}"
            self.status.set("error", self.load_error)

    def _run(self) -> None:
        try:
            self._load()
        finally:
            self._ready.set()
        while True:
            _, _, job = self._q.get()
            if self.load_error and time.time() - self._load_at >= RELOAD_AFTER_S:
                self._load()  # a failed download or a race with another process may since have cleared
            if self.load_error:
                result = {"status": "error", "error": self.load_error}
            else:
                try:
                    result = self._voice(job)
                except Exception as e:
                    log.exception("speech failed for %s", job.key)
                    result = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            with self._lock:
                callbacks = self._waiting.pop(job.key, [])
            for cb in callbacks:
                try:
                    cb(result)
                except Exception:
                    log.exception("speech callback failed")

    def _voice(self, job: _Job) -> dict:
        t0 = time.time()
        audio, tokens = self.backend.synth(job.spoken.text, job.voice, job.speed)
        compute = time.time() - t0
        duration = len(audio) / SAMPLE_RATE
        words = timed_words(job.spoken, tokens)
        result = {
            "status": "ready",
            "key": job.key,
            "duration": round(duration, 3),
            "words": words,
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_dir / f"{job.key}.part.wav"
        sf.write(tmp, np.clip(audio, -1, 1), SAMPLE_RATE, subtype="PCM_16", format="WAV")
        tmp.rename(self.audio_path(job.key))
        (self.cache_dir / f"{job.key}.json").write_text(json.dumps(result))
        self.stats["jobs"] += 1
        self.stats["audio_s"] += duration
        self.stats["compute_s"] += compute
        log.info("voiced %s: %.2fs audio in %.2fs", job.key[:8], duration, compute)
        return {**result, "compute": round(compute, 3)}


def timed_words(spoken: Spoken, tokens: list[Token]) -> list[dict]:
    """Locate each spoken token in the spoken text, map it to display offsets, and merge
    tokens that land on the same display range (one identifier read as several words)."""
    text = spoken.text
    low = text.lower()
    cursor = 0
    words: list[dict] = []
    for tk in tokens:
        t = tk.text.strip()
        if not t or not re.search(r"\w", t):
            continue
        idx = low.find(t.lower(), cursor)
        if idx == -1 or idx - cursor > 60:
            continue
        a, b = idx, idx + len(t)
        cursor = b
        if text.startswith("](/", b):  # misaki's inline pronunciation: its phonemes are not words
            cursor = text.index(")", b) + 1
        d0, d1 = spoken.to_display(a, b)
        if words and words[-1]["c0"] == d0 and words[-1]["c1"] == d1:
            words[-1]["e"] = round(tk.end, 3)
            continue
        if words and d0 < words[-1]["c1"]:  # overlapping display range: extend the previous word
            words[-1]["c1"] = max(words[-1]["c1"], d1)
            words[-1]["e"] = round(tk.end, 3)
            continue
        words.append({"s": round(tk.start, 3), "e": round(tk.end, 3), "c0": d0, "c1": d1})
    return words


LEAD_INTO_PAUSE = 0.4  # seconds a cue may move earlier, into the silence before its word


def cue_time(char_at: int, words: list[dict], duration: float, text_len: int) -> float:
    """When the pointer should move for a cue at a display offset: the start of the first word
    at or after it, or, when that word follows a pause (a new sentence), as the pause begins,
    so the viewer is looking there when the speaker starts."""
    prev_end = 0.0
    for w in words:
        if w["c1"] > char_at:
            return max(prev_end, w["s"] - LEAD_INTO_PAUSE) if w["s"] - prev_end > 0.15 else w["s"]
        prev_end = w["e"]
    if text_len:
        return duration * min(1.0, char_at / text_len)
    return 0.0
