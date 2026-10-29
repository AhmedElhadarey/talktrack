"""Egyptian Arabic narration: the voice router, timings from audio, the Arabic reading of
narration, ambiguous words, add_pronunciation and the Arabic guide. No model is loaded."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from talktrack import pronounce
from talktrack.model import Walkthrough
from talktrack.pronounce import arabic_flags, speakable
from talktrack.resources import GB, Budget, Machine
from talktrack.server import guide
from talktrack.speech import Narrator, Status, make_backend
from talktrack.speech import voxcpm as vox
from talktrack.speech.align import sentence_tokens
from talktrack.speech.base import SAMPLE_RATE, VOICES, Token, lang_of
from talktrack.speech.fake import FakeBackend
from talktrack.speech.router import Voices

# -- voices and the router ------------------------------------------------------------------------


def test_arabic_voices_are_listed_and_shipped():
    for v in ("ar_eg_female", "ar_eg_male"):
        assert v in VOICES and lang_of(v) == "ar"
        assert (vox.VOICE_DIR / f"{v}.flac").is_file()
    assert lang_of("af_heart") == lang_of("bm_george") == "en"


class Named(FakeBackend):
    def __init__(self, model_id: str) -> None:
        super().__init__()
        self.model_id = model_id
        self.calls: list[str] = []

    def synth(self, text, voice, speed):
        self.calls.append(voice)
        return super().synth(text, voice, speed)


def test_router_sends_arabic_voices_to_the_arabic_engine():
    en, ar = Named("kokoro"), Named("vox")
    v = Voices(en, ar)
    v.synth("hello", "af_heart", 1.0)
    v.synth("أهلا", "ar_eg_male", 1.0)
    assert en.calls == ["af_heart"] and ar.calls == ["ar_eg_male"]
    assert v.model_for("ar_eg_male") == "vox" and v.model_for("af_heart") == "kokoro" == v.model_id


def test_router_without_arabic_says_why():
    v = Voices(Named("kokoro"), None, "needs Apple silicon")
    assert v.unavailable("ar_eg_female") == "needs Apple silicon" and v.unavailable("af_heart") == ""
    with pytest.raises(RuntimeError, match="Apple silicon"):
        v.synth("أهلا", "ar_eg_female", 1.0)


def test_make_backend_routes(monkeypatch, tmp_path):
    fake = make_backend("fake", tmp_path, Status())
    assert isinstance(fake, Voices) and fake.arabic is fake.english
    monkeypatch.setattr(vox, "available", lambda: False)
    onnx_only = make_backend("onnx", tmp_path, Status())
    assert onnx_only.arabic is None and "Apple silicon" in onnx_only.unavailable("ar_eg_male")


def test_cache_key_names_the_arabic_model(tmp_path):
    n = Narrator(Voices(Named("kokoro"), Named("vox")), tmp_path)
    k_ar, _ = n.prepare("أهلا بيكم", voice="ar_eg_male")
    n.backend.arabic.model_id = "vox-2"
    assert n.prepare("أهلا بيكم", voice="ar_eg_male")[0] != k_ar  # a new Arabic model is new audio
    k_en, _ = n.prepare("hello", voice="af_heart")
    n.backend.arabic.model_id = "vox-3"
    assert n.prepare("hello", voice="af_heart")[0] == k_en  # English audio is untouched


def test_player_gets_the_language():
    assert Walkthrough("a", "b", "c", "T", voice="ar_eg_female").public()["lang"] == "ar"
    assert Walkthrough("a", "b", "c", "T").public()["lang"] == "en"


# -- the VoxCPM2 engine, without the model ---------------------------------------------------------


def test_sentences_split_on_arabic_and_latin_marks():
    assert vox.sentences("أهلا بيكم. ازيك؟ تمام!\nسطر جديد") == ["أهلا بيكم.", "ازيك؟", "تمام!", "سطر جديد"]


def test_voxcpm_refuses_to_load_without_free_memory(monkeypatch):
    monkeypatch.setattr(vox, "budget", lambda: Budget(Machine(16 * GB, 3 * GB, 8), 8 * GB))
    with pytest.raises(RuntimeError, match="at least 4 GB of free memory"):
        vox.VoxBackend()._ensure()


def test_voxcpm_refuses_over_the_memory_ceiling(monkeypatch):
    monkeypatch.setattr(vox, "budget", lambda: Budget(Machine(8 * GB, 6 * GB, 8), 4 * GB))
    with pytest.raises(RuntimeError, match="TALKTRACK_MAX_MEMORY"):
        vox.VoxBackend()._ensure()


def test_a_late_idle_timer_keeps_a_model_in_use():
    b = vox.VoxBackend()
    b._model, b._uses = object(), 2
    b.unload(1)  # fired for use 1, but use 2 came after it
    assert b._model is not None
    b.unload(2)
    assert b._model is None


def test_cache_key_names_the_voice_clip():
    b = vox.VoxBackend()
    assert b.key_for("ar_eg_male") != b.key_for("ar_eg_female") and b.key_for("ar_eg_male").startswith(b.model_id)


def test_voxcpm_rejects_an_unknown_voice():
    with pytest.raises(ValueError, match="unknown voice"):
        vox.VoxBackend().synth("أهلا", "ar_xx", 1.0)


def test_arabic_memory_cap_is_six_gb_under_the_ceiling():
    assert Budget(Machine(16 * GB, 6 * GB, 8), 8 * GB).arabic_speech_limit() == 6 * GB
    assert Budget(Machine(8 * GB, 4 * GB, 8), 4 * GB).arabic_speech_limit() == 4 * GB


# -- timings from audio ----------------------------------------------------------------------------


def _clip(*parts: tuple[str, float]) -> np.ndarray:
    """("tone"|"gap", seconds) pieces as audio."""
    out = []
    for kind, s in parts:
        n = int(s * SAMPLE_RATE)
        t = np.arange(n) / SAMPLE_RATE
        out.append(0.3 * np.sin(2 * np.pi * 200 * t) if kind == "tone" else np.zeros(n))
    return np.concatenate(out).astype(np.float32)


def test_timings_split_clauses_at_the_pauses():
    audio = _clip(("gap", 0.2), ("tone", 1.0), ("gap", 0.5), ("tone", 2.0), ("gap", 0.2))
    toks = sentence_tokens("الفكرة بسيطة، انت بتكتب الشرايح", audio, offset=1.0)
    assert [t.text for t in toks] == ["الفكرة", "بسيطة", "انت", "بتكتب", "الشرايح"]
    assert toks[0].start == pytest.approx(1.2, abs=0.05)  # leading silence skipped, offset added
    assert toks[1].end <= 2.2 + 0.05 and toks[2].start >= 2.7 - 0.05  # the comma is the pause
    assert toks[-1].end <= 4.7 + 0.05
    assert all(a.end <= b.start for a, b in zip(toks, toks[1:], strict=False))


def test_timings_without_a_pause_spread_by_length():
    audio = _clip(("tone", 2.0))
    toks = sentence_tokens("كلمة طويلةجداجدا، قصير", audio, offset=0.0)
    assert len(toks) == 3 and toks[1].end - toks[1].start > toks[2].end - toks[2].start


def test_harakat_stay_inside_a_word():
    toks = sentence_tokens("الشريحة بِتِتْعِرِض", _clip(("tone", 1.0)), 0.0)
    assert [t.text for t in toks] == ["الشريحة", "بِتِتْعِرِض"]


# -- reading Arabic narration ----------------------------------------------------------------------


def test_say_override_and_display_offsets():
    text = "وأول ما تخلص الشريحة بتتعرض على طول."
    a = text.index("بتتعرض")
    sp = speakable(text, say=[(a, a + 6, "بِتِتْعِرِض")], lang="ar")
    assert "بِتِتْعِرِض" in sp.text and "[" not in sp.text
    s = sp.text.index("بِتِتْعِرِض")
    assert sp.to_display(s, s + len("بِتِتْعِرِض")) == (a, a + 6)


def test_lexicon_matches_after_prefixes():
    lex = {"المحرر": "الْمُحَرِّر", "talktrack": "توك تراك", "kubectl": "/kjˈubkˌʌtəl/"}
    sp = speakable("والمحرر للمحرر بالمحرر talktrack kubectl", lexicon=lex, lang="ar")
    assert sp.text.startswith("والْمُحَرِّر للْمُحَرِّر بالْمُحَرِّر توك تراك ")
    assert sp.text.endswith("kubectl")  # phonemes are for English voices only
    assert "محررات" == speakable("محررات", lexicon=lex, lang="ar").text  # no match inside a longer word


def test_words_with_one_reading_are_marked_and_the_user_wins():
    assert speakable("والمؤشر بيتحرك", lang="ar").text == "والْمُؤَشِّر بيتحرك"
    assert speakable("المؤشر", lexicon={"المؤشر": "المؤشّر"}, lang="ar").text == "المؤشّر"


def test_code_in_arabic_narration_is_read_as_words():
    text = "بننادي clear_cache وبعدين mx.eval"
    code = [(text.index("clear_cache"), text.index("clear_cache") + 11), (text.index("mx.eval"), len(text))]
    assert speakable(text, code=code, lang="ar").text == "بننادي clear cache وبعدين mx dot eval"


def test_english_narration_is_unchanged_by_the_arabic_branch():
    assert speakable("the cache is 25 GB", lang="en").text == speakable("the cache is 25 GB").text


def test_flags_list_words_whose_reading_depends_on_meaning():
    text = "ودي مهمة، والشريحة بتتعرض في المحرر."
    flags = dict(arabic_flags(text))
    assert set(flags) == {"مهمة", "بتتعرض", "المحرر"}
    assert any("a task" in r for r in flags["مهمة"])
    a = text.index("بتتعرض")
    covered = dict(arabic_flags(text, say=[(a, a + 6, "بِتِتْعِرِض")], lexicon={"المحرر": "الْمُحَرِّر"}))
    assert set(covered) == {"مهمة"}
    assert [w for w, _ in arabic_flags("وللمحرر")] == ["وللمحرر"]  # named as written
    assert dict(arabic_flags("وللمهمة"))["وللمهمة"][0] == "وللمَهَمَّة (a task)"  # readings keep the prefix


def test_saved_word_matches_with_al_and_its_prefixes():
    lex = {"محرك": "مُحَرِّك", "المؤشر": "اَلْمُؤَشِّر"}
    said = {t: speakable(t, lexicon=lex, lang="ar").text for t in ("المحرك", "بالمحرك", "للمحرك", "ومحرك", "محركات", "للمؤشر")}
    assert said["المحرك"] == "المُحَرِّك" and said["بالمحرك"] == "بالمُحَرِّك" and said["للمحرك"] == "للمُحَرِّك"
    assert said["ومحرك"] == "ومُحَرِّك" and said["محركات"] == "محركات"
    assert said["للمؤشر"] == "للْمُؤَشِّر"  # ال after ل loses its alif, marks and all


def test_shipped_word_list_parses():
    one, several = pronounce._ar_words()
    assert one and several and all(len(r) >= 2 for r in several.values())
    assert not set(one) & set(several)


# -- the store and the tools -----------------------------------------------------------------------


async def test_arabic_walkthrough_reports_ambiguous_words(store, service):
    r = await service.start("شرح", "landscape", "ar_eg_female", 1.0, None)
    out = await service.add(r["handle"], "# شرح\n\nالنهارده هنشرح مهمة صغيرة.\n")
    codes = [i["code"] for i in out["issues"]]
    assert "arabic_reading" in codes
    issue = next(i for i in out["issues"] if i["code"] == "arabic_reading")
    assert "مهمة" in issue["message"] and "say=" in issue["fix"]


async def test_english_walkthrough_never_reports_arabic_words(store, service):
    r = await service.start("T", "landscape", "", 1.0, None)
    out = await service.add(r["handle"], "# T\n\nمهمة\n")
    assert "arabic_reading" not in [i["code"] for i in out["issues"]]


async def test_arabic_voice_is_refused_where_it_cannot_speak(tmp_path, browser_ready):
    from talktrack.store import Store

    n = Narrator(Voices(FakeBackend(), None, "Arabic voices need a Mac with Apple silicon"), tmp_path / "audio")
    s = Store(tmp_path / "data", "http://localhost:4799", n)
    with pytest.raises(ValueError, match="cannot speak on this machine.*Apple silicon"):
        s.create("T", voice="ar_eg_male")


async def test_add_pronunciation_saves_replaces_and_revoices(store, service, voiced):
    r = await service.start("شرح", "landscape", "ar_eg_female", 1.0, None)
    await service.add(r["handle"], "# شرح\n\nالشريحة بتتعرض على طول.\n")
    s = store.by_handle(r["handle"])
    await voiced(s)
    before = s.w.slides[0].says[0].key
    out = await service.add_pronunciation("بتتعرض", "بِتِتْعِرِض", r["handle"])
    lex = Path(out["file"])
    assert lex.read_text(encoding="utf-8") == "بتتعرض = بِتِتْعِرِض\n" and not out["replaced"]
    assert out["revoiced"] == 1 and s.w.slides[0].says[0].key != before
    assert store.narrator.lexicon["بتتعرض"] == "بِتِتْعِرِض"
    marked = await service.add_pronunciation("بِتِتْعِرِض", "بِتِتْعِرِض", r["handle"])  # given with harakat
    assert marked["word"] == "بتتعرض" and marked["replaced"]
    again = await service.add_pronunciation("بتتعرض", "بِتِتْعَرَّض")
    assert again["replaced"] and lex.read_text(encoding="utf-8") == "بتتعرض = بِتِتْعَرَّض\n"


async def test_add_pronunciation_checks_before_writing(store, service):
    with pytest.raises(ValueError, match="unknown handle"):
        await service.add_pronunciation("كلمة", "كِلْمَة", "nope.nope")
    with pytest.raises(ValueError, match="'='"):
        await service.add_pronunciation("a=b", "x")
    with pytest.raises(ValueError, match="'#'"):
        await service.add_pronunciation("#كلمة", "x")
    assert not (store.data_dir / "lexicon.txt").exists()


def test_add_pronunciation_tool_is_listed():
    from talktrack.server import texts

    t = texts()["tools"]["add_pronunciation"]
    assert set(t["args"]) == {"word", "say", "handle"}


def test_guide_adds_arabic_rules_only_for_arabic():
    ar, en = guide("landscape", lang="ar"), guide("landscape")
    assert "## Arabic narration" in ar and "## Arabic narration" not in en
    assert ar.startswith(en.rstrip("\n"))
    assert "add_pronunciation" in ar and "{{" not in ar


def test_timed_words_keep_working_with_router_tokens(tmp_path):
    from talktrack.speech.narrator import timed_words

    sp = speakable("الفكرة بسيطة", lang="ar")
    words = timed_words(sp, [Token("الفكرة", 0.0, 0.5), Token("بسيطة", 0.6, 1.0)])
    assert [(w["c0"], w["c1"]) for w in words] == [(0, 6), (7, 12)]


def test_issue_code_is_documented():
    spec = (Path(__file__).parent.parent / "docs" / "spec.md").read_text()
    assert "`arabic_reading`" in spec


def test_arabic_model_downloads_after_speech_went_offline(monkeypatch, tmp_path):
    import huggingface_hub
    from huggingface_hub import constants

    from talktrack.speech import assets

    calls = []

    def snap(repo, allow_patterns=None, local_files_only=False, tqdm_class=None):
        calls.append((local_files_only, constants.HF_HUB_OFFLINE))
        if local_files_only:
            raise FileNotFoundError("not cached")
        return str(tmp_path)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snap)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", False)
    monkeypatch.setattr(assets, "_went_offline", False)
    assets.go_offline()  # Kokoro is loaded
    with pytest.raises(RuntimeError, match="HF_HUB_OFFLINE"):
        assets.hf_snapshot("x/y", ["*"], None, "x")
    assert assets.hf_snapshot("x/y", ["*"], None, "x", online=True) == tmp_path
    assert calls[-1] == (False, False) and constants.HF_HUB_OFFLINE  # online for the download, offline after
    monkeypatch.setattr(assets, "_went_offline", False)  # the user set HF_HUB_OFFLINE: respected
    with pytest.raises(RuntimeError, match="HF_HUB_OFFLINE"):
        assets.hf_snapshot("x/y", ["*"], None, "x", online=True)


def test_arabic_labels_get_their_own_ids():
    from talktrack.build import slugify
    from talktrack.kinds.base import slug

    assert slug("الشبكة") != slug("الكاش") and slug("الكَاش السريع") == "الكاش-السريع"
    assert slugify("مسار الطلب") == "مسار-الطلب"
    assert slug("Cache hit") == "cache-hit" and slugify("!!!") == "slide"  # English ids unchanged


def test_player_page_is_rtl_from_the_first_byte():
    from talktrack.server import render_player

    ar = render_player("شرح", "landscape", "", "static", live=False, lang="ar")
    en = render_player("T", "landscape", "", "static", live=False)
    assert '<html lang="ar" dir="rtl"' in ar and '<html lang="en" data-theme' in en and "{{" not in ar + en
