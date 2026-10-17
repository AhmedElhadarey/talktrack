"""Pronunciation: narration rewritten into speakable text, and the map from spoken back to display text.

No speech model is needed. Timings are simulated with one token per spoken word, the way misaki
returns them, and run through the narrator's real `timed_words`.
"""

from __future__ import annotations

import re
import time

import pytest

from showntell import pronounce
from showntell.format import parse_narration
from showntell.pronounce import Spoken, speak_code, speakable, split_identifier
from showntell.speech.base import Token
from showntell.speech.narrator import timed_words

INLINE = re.compile(r"\[([^\]]+)\]\(/([^)]*)/\)")  # misaki's inline pronunciation, as speakable writes it


def voice(src: str, lexicon: dict | None = None) -> tuple[str, Spoken]:
    n = parse_narration(src)
    return n.display, speakable(n.display, n.code, n.say, lexicon or {})


def said(src: str, lexicon: dict | None = None) -> str:
    """Spoken text with inline pronunciations shown as `word/phonemes/`, for readable asserts."""
    return INLINE.sub(r"\1/\2/", voice(src, lexicon)[1].text)


def fake_tokens(text: str) -> list[Token]:
    """The tokens misaki would time: words, numbers and inline pronunciations (as their word)."""
    out = []
    for k, m in enumerate(re.finditer(r"\[([^\]]+)\]\(/[^)]*/\)|[\w'’.-]*\w", text)):
        out.append(Token(m.group(1) or m.group(), k * 0.3, k * 0.3 + 0.25))
    return out


def check_map(display: str, sp: Spoken) -> None:
    """Segments tile both strings in order; verbatim segments copy the display exactly."""
    assert sp.segs, "no segments"
    assert len(sp.copied) == len(sp.segs)
    s_pos = d_pos = 0
    for k, (sa, sb, da, db) in enumerate(sp.segs):
        assert (sa, da) == (s_pos, d_pos), f"gap or overlap at spoken {sa}, display {da}"
        assert sb >= sa and db >= da
        if sp.copied[k]:
            assert sp.text[sa:sb] == display[da:db]
        s_pos, d_pos = sb, db
    assert (s_pos, d_pos) == (len(sp.text), len(display))
    # every spoken character maps into its segment's display range: exactly where copied,
    # onto the whole rewritten range otherwise
    for k, (sa, sb, da, db) in enumerate(sp.segs):
        for a in range(sa, sb):
            d0, d1 = sp.to_display(a, a + 1)
            assert da <= d0 <= d1 <= db
            assert (d0, d1) == ((da + a - sa, da + a - sa + 1) if sp.copied[k] else (da, db))


SAMPLES = [
    "Every request checks the cache before it touches the database.",
    "Call `useEffect` after the first render, and read `HF_HUB_OFFLINE` from the environment.",
    "The check `a == b` fails, so `a != b` holds, and `x => x * 2` doubles it.",
    "Open `config.py`, `package.json` and `src/server/routes.ts`, then run `--port 8080`.",
    "The request takes 5ms, the page 1.5s, and each worker 200MB at 60fps and 24kHz, 3x faster.",
    "We upgraded to v2.3.1 on Python 3.13; sorting is O(n log n) and a lookup O(1).",
    "Docs live at https://docs.example.com/guide and the code at github.com/acme/widgets.",
    "The cluster runs on Kubernetes with kubectl, nginx, Redis and a lockfile in the monorepo.",
    "Zustand and Jotai keep state; the formatter, the fzf picker and Rahim's tool agree.",
    'The [checking the dictionary](#L12 "if key") step, then [nginx]{say="engine ex"} serves it.',
    "Each `struct`s field, the `Vec<String>`, `**kwargs`, `std::io::Result` and `0x1F`.",
    "About ~20 ms, issue #123, e.g. the `README.md`, 127.0.0.1 and `--dry-run`.",
]


# -- the map from spoken to display text ---------------------------------------------------------


@pytest.mark.parametrize("src", SAMPLES)
def test_segments_cover_both_strings_in_order(src):
    display, sp = voice(src)
    check_map(display, sp)


@pytest.mark.parametrize("src", SAMPLES)
def test_every_caption_word_lands_inside_the_display(src):
    display, sp = voice(src)
    words = timed_words(sp, fake_tokens(sp.text))
    assert words
    for w in words:
        assert 0 <= w["c0"] < w["c1"] <= len(display), w
    for prev, cur in zip(words, words[1:], strict=False):
        assert prev["c1"] <= cur["c0"] and prev["s"] <= cur["s"]
    # every display word is reached by some caption word
    for m in re.finditer(r"\w+", display):
        assert any(w["c0"] < m.end() and m.start() < w["c1"] for w in words), m.group()


@pytest.mark.parametrize("src", ["Call `getUserById` now.", "Read `HF_HUB_OFFLINE` now."])
def test_identifier_maps_to_one_caption_word(src):
    display, sp = voice(src)
    name = re.search(r"`(.*)`", src).group(1)
    (word,) = [w for w in timed_words(sp, fake_tokens(sp.text)) if display[w["c0"] : w["c1"]] == name]
    assert word["e"] - word["s"] > 0.5  # one caption word spans all its spoken words


def test_plain_prose_is_copied_verbatim():
    display, sp = voice("Every request checks the cache before it touches the database.")
    assert sp.text == display
    assert sp.segs == [(0, len(display), 0, len(display))] and sp.copied == [True]


def test_spoken_without_a_copied_list_falls_back_to_lengths():
    sp = Spoken("ab cd", [(0, 2, 0, 2), (2, 5, 2, 9)])
    assert sp.to_display(0, 1) == (0, 1) and sp.to_display(3, 4) == (2, 9)
    assert Spoken("plain").to_display(1, 3) == (1, 3)


def test_to_display_round_trips_verbatim_text():
    display, sp = voice("Read `HF_HUB_OFFLINE` and the cache.")
    i = sp.text.index("cache")
    assert sp.to_display(i, i + 5) == (display.index("cache"), display.index("cache") + 5)
    j = sp.text.index("HF")
    assert sp.to_display(j, j + 2) == (display.index("HF_HUB_OFFLINE"), display.index(" and"))


def test_empty_text():
    assert speakable("").text == ""


# -- code spans -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "spoken"),
    [
        ("useEffect", "use effect"),
        ("HF_HUB_OFFLINE", "HF HUB offline"),
        ("max_retries", "max retries"),
        ("dry-run", "dry run"),
        ("render()", "render"),
        ("os.path.join", "[OS](/ˌOˈɛs/) dot path dot join"),
        ("len(items)", "len of items"),
        ("a == b", "[a](/ˈA/) double equals b"),
        ("x != y", "x not equals y"),
        ("x => x", "x arrow x"),
        ("f() -> T", "f arrow T"),
        ("p && q || r", "p and q or r"),
        ("i <= n", "i less than or equal to n"),
        ("n >= 0", "n greater than or equal to 0"),
        ("count += 1", "count plus equals 1"),
        ("x ** 2", "x to the power of 2"),
        ("*args", "star [args](/ˈɑɹɡz/)"),
        ("std::io", "STD colon colon io"),
        ("Vec<String>", "[Vec](/vˈɛk/) of string"),
        ("str | None", "string or none"),
        ("config.py", "[config](/kˈɑnfɪɡ/) dot pie"),
        ("App.tsx", "app dot TSX"),
        ("package.json", "package dot jason"),
        ("Cargo.toml", "cargo dot [toml](/tˈɑməl/)"),
        (".env", "dot env"),
        ("__init__.py", "dunder init dot pie"),
        ("src/app/main.go", "source slash app slash main dot go"),
        ("~/.zshrc", "tilde slash dot ZSHRC"),
        ("--port", "dash dash port"),
        ("-rf", "dash RF"),
        ("0x1F", "hex 1 F"),
        ("v2.3.1", "version 2 point 3 point 1"),
        ("5ms", "5 milliseconds"),
        ("JSON", "jason"),
        ("API", "API"),
        ("C++", "C plus plus"),
        ("Next.js", "next JS"),
        ("k8s", "[k8s](/kˈAts/)"),
        ("npm", "NPM"),
    ],
)
def test_code_reading(code, spoken):
    assert speak_code(code) == spoken


def test_split_identifier():
    assert split_identifier("getUserById") == "get user by id"
    assert split_identifier("HTTPServer") == "HTTP server"
    assert split_identifier("__main__") == "dunder main"
    assert split_identifier("int64") == "int 64"


def test_code_span_takes_a_plural_s():
    assert said("Each `struct`s field.") == "Each structs/stɹˈʌkts/ field."


# -- prose ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "spoken"),
    [
        ("It takes 5ms.", "It takes 5 milliseconds."),
        ("It takes 1 ms.", "It takes 1 millisecond."),
        ("It loads in 1.5s.", "It loads in 1.5 seconds."),
        ("It uses 200MB and 4KB pages.", "It uses 200 megabytes and 4 kilobytes pages."),
        ("It runs at 60fps with audio at 24kHz.", "It runs at 60 FPS with audio at 24 kilohertz."),
        ("It is 3x faster on 16 GB.", "It is 3 times faster on 16 gigabytes."),
        ("Downloads run at 100 Mbps.", "Downloads run at 100 megabits per second."),
        ("It took ~20 ms.", "It took about 20 milliseconds."),
        ("A 7B model.", "A 7 billion model."),
        ("We moved to v2.3.1 today.", "We moved to version 2 point 3 point 1 today."),
        ("It needs Python 3.13 now.", "It needs Python 3 point 13 now."),
        ("Sorting is O(n log n).", "Sorting is order n log n."),
        ("A lookup is O(1).", "A lookup is order 1."),
        ("The scan is O(n^2).", "The scan is order n squared."),
        ("It is Θ(n).", "It is theta of n."),
        ("See https://docs.example.com/guide now.", "See docs dot example dot com slash guide now."),
        ("Edit src/app/main.py today.", "Edit source slash app slash main dot pie today."),
        ("Bind 127.0.0.1 here.", "Bind 1 2 7 dot 0 dot 0 dot 1 here."),
        ("See issue #123.", "See issue number 123."),
        ("Run it with --verbose.", "Run it with dash dash verbose."),
        ("It runs on AWS and the OS.", "It runs on [AWS](/ˌAdˌʌbᵊljuˈɛs/) and the [OS](/ˌOˈɛs/)."),
        ("Use JSON or YAML.", "Use jason or [YAML](/jˈæməl/)."),
        ("Read from stdin.", "Read from standard in."),
        ("Run kubectl behind nginx.", "Run cube control behind engine X."),
        ("Then call os.path.join on it.", "Then call [OS](/ˌOˈɛs/) dot path dot join on it."),
        ("Use e.g. this, i.e. that, etc. and so on.", "Use e.g. this, i.e. that, etc. and so on."),
        ("The README.md file.", "The reed me dot MD file."),
        ("TCP/IP and and/or stay.", "TCP/IP and and/or stay."),
    ],
)
def test_prose_reading(src, spoken):
    assert voice(src)[1].text == spoken


def test_a_sentence_of_ordinary_words_is_untouched():
    src = "The method starts by checking the dictionary, and on a miss it calls the loader."
    assert said(src) == src


# -- words misaki does not know -----------------------------------------------------------------------


def test_listed_words_use_the_list():
    assert said("The kubectl tool.") == "The cube control tool."
    assert said("A Postgres table.") == "A Postgres/pˈOstɡɹɛs/ table."
    assert said("Two enums.") == "Two enums/ˈinəmz/."


def test_compound_words_are_built_from_dictionary_pieces():
    out = said("The github bitfield.")
    assert "github/ɡˈɪthˌʌb/" in out and "bitfield/bˈɪtfˌild/" in out


def test_suffixes_join_a_known_stem():
    assert "formatter/fˈɔɹmˌætəɹ/" in said("The formatter runs.")


def test_unknown_names_are_sounded_out():
    out = said("Rahim wrote it.")
    m = re.match(r"Rahim/([^/]+)/ wrote it\.", out)
    assert m and "ˈ" in m.group(1)


def test_words_without_vowels_are_spelled():
    assert said("Use fzf here.") == "Use FZF here."


def test_no_word_is_left_silent():
    """Every word in the spoken text is in misaki's dictionary, spelled, or pronounced inline."""
    text = " ".join(voice(s)[1].text for s in SAMPLES)
    text = INLINE.sub(" ", text)
    for w in re.findall(r"[A-Za-z][A-Za-z']*", text):
        pieces = pronounce._pieces(w.replace("'s", ""))
        assert all(pronounce.known(p) for p in pieces), w


def test_inline_pronunciations_parse_as_misaki_expects():
    misaki_en = pytest.importorskip("misaki.en")
    for src in SAMPLES:
        sp = voice(src)[1]
        text, tokens, features = misaki_en.G2P.preprocess(sp.text)
        assert text == INLINE.sub(r"\1", sp.text).lstrip()
        for k, f in features.items():
            assert isinstance(f, str) and f.startswith("/"), (src, f)
            assert set(f.lstrip("/")) <= pronounce._PHONEMES, f
            assert tokens[k] in sp.text


# -- overrides ----------------------------------------------------------------------------------------


def test_say_override_wins():
    assert said('Then [nginx]{say="engine ex"} serves it.') == "Then engine ex serves it."
    assert said('Then [`a == b`]{say="a equals b"} holds.') == "Then a equals b holds."


def test_say_override_only_fills_in_silent_words():
    assert said('Then [it]{say="kubectl applies JSON"} runs.') == "Then cube control applies JSON runs."


def test_british_voices_get_british_phonemes():
    us = said("Kafka and Postgres.")
    gb = INLINE.sub(r"\1/\2/", speakable("Kafka and Postgres.", british=True).text)
    assert us == "Kafka/kˈɑfkə/ and Postgres/pˈOstɡɹɛs/."
    assert gb == "Kafka/kˈɑːfkə/ and Postgres/pˈQstɡɹɛs/."
    assert pronounce.to_british("kˈɑnfɪɡ") == "kˈɒnfɪɡ" and pronounce.to_british("ˈɑɹɡz") == "ˈɑːɡz"


def test_user_lexicon_applies_in_prose_and_code():
    lex = {"showntell": "show and tell", "Acme": "/ˈækmi/"}
    assert said("The showntell player and `showntell`.", lex) == "The show and tell player and show and tell."
    assert said("Acme ships.", lex) == "Acme/ˈækmi/ ships."
    assert said("Run `showntell.app` now.", lex) == "Run show and tell dot app now."


# -- the word list ---------------------------------------------------------------------------------------


def test_word_list_entries_are_well_formed():
    loose, exact = pronounce._word_list()
    assert len(loose) > 300
    for word, value in {**loose, **exact}.items():
        if value.startswith("/"):
            assert value.endswith("/") and set(value.strip("/")) <= pronounce._PHONEMES | {"ː"}, (word, value)
            assert value.count("ˈ") >= 1, (word, value)
        else:
            for w in value.split():
                ok = w.isdigit() or (w.isupper() and w.isalpha()) or pronounce._lookup(w) or pronounce._listed_value(w) is not None
                assert ok, f"{word} = {value}: misaki does not know {w!r}"


# -- speed -------------------------------------------------------------------------------------------------


def test_a_paragraph_takes_under_20_ms_after_warm_up():
    pronounce.warm()
    src = (
        "The `parse_narration` helper reads 40 words of prose: it calls `os.path.join`, checks "
        "that the request took 5ms, loads config.yaml through Pydantic, sends JSON to the API, "
        "and logs a Qwerkly frobnicator and the zindlemoss widget in O(n log n) time, "
        "so each caption word lands where the agent wrote it."
    )
    n = parse_narration(src)
    best = float("inf")
    for _ in range(3):
        pronounce.say_word.cache_clear()
        pronounce._guess.cache_clear()
        t0 = time.perf_counter()
        speakable(n.display, n.code, n.say, {})
        best = min(best, time.perf_counter() - t0)
    assert best < 0.020, f"{best * 1000:.1f} ms"


def test_word_after_an_inline_pronunciation_keeps_its_caption():
    display, sp = voice("The Vite it builds.")  # "it" can also be found inside /vˈit/
    words = timed_words(sp, fake_tokens(sp.text))
    assert any(display[w["c0"] : w["c1"]] == "it" for w in words)
