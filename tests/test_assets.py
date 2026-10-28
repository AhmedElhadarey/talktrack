"""Model downloads: two processes fetching the same model at once must both succeed."""

from __future__ import annotations

import io
import threading
import zipfile

import spacy

from talktrack.speech import assets
from talktrack.speech.assets import SPACY_NAME, SPACY_VERSION, ensure_spacy, spacy_dir


def _wheel() -> bytes:
    prefix = f"{SPACY_NAME}/{SPACY_NAME}-{SPACY_VERSION}/"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(prefix + "config.cfg", "[nlp]\n")
        for i in range(50):
            z.writestr(prefix + f"tok2vec/part{i}", b"x" * 1000)
    return buf.getvalue()


def test_concurrent_spacy_downloads_both_succeed(tmp_path, monkeypatch):
    """The daemon and `talktrack doctor` both fetch the model on a fresh machine: one must not
    pull the other's half-extracted folder away (FileNotFoundError on tok2vec/model)."""
    data = _wheel()
    both_downloaded = threading.Barrier(2)

    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout):
        both_downloaded.wait(5)  # extract at the same moment
        return Response(data)

    monkeypatch.setattr(assets.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(spacy.util, "is_package", lambda name: False)
    monkeypatch.setattr(spacy, "load", spacy.load)
    monkeypatch.setattr(spacy, "_talktrack_redirect", None, raising=False)

    results, errors = [], []

    def run():
        try:
            results.append(ensure_spacy(tmp_path))
        except Exception as e:  # noqa: BLE001 - the test reports any failure
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, errors
    target = spacy_dir(tmp_path)
    assert results == [target, target]
    assert (target / "config.cfg").is_file() and len(list((target / "tok2vec").iterdir())) == 50
    assert [p.name for p in target.parent.iterdir()] == [target.name]  # no leftover .part folders


def test_narrator_retries_a_failed_load(tmp_path, monkeypatch):
    """A load that failed (a download cut short, a race) is tried again by a later narration,
    instead of failing every narration until the daemon restarts."""
    from talktrack.speech import narrator as narrator_mod
    from talktrack.speech.fake import FakeBackend
    from talktrack.speech.narrator import Narrator

    class Flaky(FakeBackend):
        loads = 0

        def load(self) -> None:
            Flaky.loads += 1
            if Flaky.loads == 1:
                raise FileNotFoundError("model.part/tok2vec/model")

    n = Narrator(Flaky(), tmp_path / "audio")
    n.start()
    assert n.wait_ready(5) and not n.ready and "FileNotFoundError" in n.load_error

    def voice() -> dict:
        got = threading.Event()
        out: dict = {}
        key, spoken = n.prepare("Hello there.")
        n.request(key, spoken, "af_heart", 1.0, 0, lambda r: (out.update(r), got.set()))
        assert got.wait(5)
        return out

    assert voice()["status"] == "error"  # within RELOAD_AFTER_S of the failure: no new attempt
    monkeypatch.setattr(narrator_mod, "RELOAD_AFTER_S", 0.0)
    assert voice()["status"] == "ready" and n.ready and Flaky.loads == 2
