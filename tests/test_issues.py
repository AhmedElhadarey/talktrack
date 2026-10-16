"""Structured findings: codes, severities, folding, player reports, and the checks added with them."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from showntell.issues import CODES, Issue, as_dict, line_text, player_issue, summarize

OPENING = "# How caching works\n\nA cache keeps answers close.\n"


def codes(issues) -> list[str]:
    return [i.code if isinstance(i, Issue) else i["code"] for i in issues]


# -- the Issue type --------------------------------------------------------------------------------


def test_issue_is_its_text_and_carries_fields():
    i = Issue("code_too_tall", "Code shows 30 lines.", "Show fewer.", slide="s", line=4)
    assert i == "Code shows 30 lines. Show fewer." and "30 lines" in i
    assert i.to_dict() == {"code": "code_too_tall", "severity": "warning", "message": "Code shows 30 lines.",
                           "fix": "Show fewer.", "slide": "s", "line": 4}


def test_unknown_code_is_refused():
    with pytest.raises(KeyError):
        Issue("made_up", "x")


def test_plain_strings_become_code_other():
    assert as_dict("old style") == {"code": "other", "severity": "warning", "message": "old style"}


def test_summarize_orders_by_severity_and_folds_repeats():
    items = [Issue("no_cues", f"n{i}").to_dict() for i in range(5)] + [Issue("cue_off_code", "bad").to_dict()]
    kept, notes = summarize(items, limit=12, per_code=3)
    assert kept[0]["code"] == "cue_off_code" and codes(kept).count("no_cues") == 3
    assert notes == ["…and 2 more `no_cues` findings"]
    kept, notes = summarize(items, limit=2, per_code=3)
    assert len(kept) == 2 and any("no_cues" in n for n in notes)


def test_line_text_marks_errors_and_names_the_code():
    assert line_text(Issue("cue_off_code", "Past the end.", "Use gutter numbers.").to_dict()) == \
        "- Error: Past the end. Use gutter numbers. (cue_off_code)"
    assert line_text(Issue("no_cues", "No cues.").to_dict()) == "- No cues. (no_cues)"


def test_player_issue_keeps_known_codes_and_trims():
    i = player_issue({"code": "text_too_small", "slide": "lookup", "message": "  shrunk   to 40% "})
    assert (i.code, i.slide, i.source, str(i)) == ("text_too_small", "lookup", "player", "shrunk to 40%")
    assert player_issue({"code": "cue_off_code", "message": "x"}).code == "player_error"  # server codes are not the player's to use
    assert player_issue({"message": ""}) is None
    assert len(player_issue({"message": "y" * 1000})) == 300


def test_every_code_is_documented_in_the_spec():
    spec = (Path(__file__).parent.parent / "docs" / "spec.md").read_text()
    missing = [c for c in CODES if f"`{c}`" not in spec]
    assert not missing, f"document these codes in docs/spec.md: {missing}"


def test_every_code_used_in_the_source_exists():
    src = Path(__file__).parent.parent / "src" / "showntell"
    used = set()
    for f in src.glob("*.py"):
        used |= set(re.findall(r"""(?:_issue|Issue)\(\s*["']([a-z_]+)["']""", f.read_text()))
    assert used and used <= set(CODES), used - set(CODES)


# -- builder checks -------------------------------------------------------------------------------


def test_builder_issues_name_slide_and_line(build):
    _, b = build("# T\n\nA.\n\n## Big {#big}\n\n" + "```python\n" + "x\n" * 30 + "```\n\nSay it.\n")
    (i,) = [i for i in b.issues if i.code == "code_too_tall"]
    assert i.slide == "big" and i.fix


@pytest.mark.parametrize("text, what", [
    ("Thanks to @octocat for this.", "a handle"),
    ("See https://example.com/x for more.", "a URL"),
    ("Commit 3f9a2c1b fixed it.", "a commit hash"),
])
def test_spoken_handles_urls_and_hashes_are_flagged(build, text, what):
    _, b = build(f"# T\n\nA.\n\n## S {{#s}}\n\n- **One**: a\n\n{text}\n")
    b.check_all()
    assert any(i.code == "spoken_handle" and what in i for i in b.issues)


@pytest.mark.parametrize("text", ["Email me at me@example.com.", "The 1024 byte limit.", "Use `3f9a2c1b` here.",
                                  "A decade of facade work."])
def test_ordinary_text_is_not_a_handle(build, text):
    _, b = build(f"# T\n\nA.\n\n## S {{#s}}\n\n- **One**: a\n\n{text}\n")
    b.check_all()
    assert "spoken_handle" not in codes(b.issues)


LIST3 = "- **Parse**: a\n- **Plan**: b\n- **Run**: c\n"


def test_build_cues_all_in_first_paragraph_are_flagged(build):
    md = f"# T\n\nA.\n\n## Steps {{#steps .build}}\n\n{LIST3}\nWe [parse](#parse), [plan](#plan) and [run](#run).\n\nThen a lot more words follow here.\n"
    _, b = build(md)
    b.check_all()
    assert "build_cues_bunched" in codes(b.issues)


def test_build_cues_out_of_order_are_flagged(build):
    md = f"# T\n\nA.\n\n## Steps {{#steps .build}}\n\n{LIST3}\nFirst [run](#run).\n\nThen [parse](#parse).\n\nThen [plan](#plan).\n"
    _, b = build(md)
    b.check_all()
    assert "build_cue_order" in codes(b.issues)


def test_build_cues_in_order_pass(build):
    md = f"# T\n\nA.\n\n## Steps {{#steps .build}}\n\n{LIST3}\nFirst [parse](#parse).\n\nThen [plan](#plan).\n\nThen [run](#run).\n"
    _, b = build(md)
    b.check_all()
    assert not {"build_cue_order", "build_cues_bunched"} & set(codes(b.issues))


def test_a_run_of_code_slides_suggests_a_diagram(build):
    slides = "".join(f"## C{i} {{#c{i}}}\n\n```python\nx = {i}\n```\n\nThe value is {i}.\n\n" for i in range(4))
    _, b = build("# T\n\nA.\n\n" + slides)
    b.check_all()
    assert "code_run" in codes(b.issues)
    _, b = build("# T\n\nA.\n\n" + slides.replace("## C2 {#c2}\n\n```python\nx = 2\n```",
                                                    "## C2 {#c2}\n\n```mermaid\nflowchart LR\n  a --> b\n```"))
    b.check_all()
    assert "code_run" not in codes(b.issues)


def test_opening_that_names_code_is_flagged(build):
    _, b = build("# T\n\nThe `RetryPolicy` class wraps `send`.\n\n## Next\n\nMore.\n")
    b.check_all()
    assert "opening_code_names" in codes(b.issues)
    _, b = build("# T\n\nFailed requests now recover on their own.\n\n## Next\n\nMore.\n")
    b.check_all()
    assert "opening_code_names" not in codes(b.issues)


def test_diff_of_a_lockfile_is_flagged(build, repo):
    repo.write("uv.lock", "a = 1\n")
    repo.commit()
    repo.write("uv.lock", "a = 2\n")
    _, b = build("# T\n\nA.\n\n## Lock\n\n```diff file=uv.lock rev=HEAD\n```\n\nIt changed.\n", root=repo.root)
    assert "generated_file" in codes(b.issues)


# -- store: timing, coverage, revisions --------------------------------------------------------------


async def started(service):
    return await service.start("How caching works", "landscape", "", 1.0, None)


async def test_a_long_still_slide_is_reported_once_voiced(service, store, voiced):
    r = await started(service)
    words = " ".join(["word"] * 90) + "."
    await service.add(r["handle"], OPENING + f"\n## Still {{#still}}\n\n```python\nx = 1\n```\n\n{words}\n")
    await voiced(store.by_handle(r["handle"]))
    a = await service.add(r["handle"], "## Next\n\nMore.\n")
    assert any(i["code"] == "still_slide" and i["slide"] == "still" for i in a["issues"])
    a = await service.add(r["handle"], "## Later\n\nMore.\n")
    assert "still_slide" not in codes(a["issues"])  # reported once


async def test_cues_break_up_a_long_slide(service, store, voiced):
    r = await started(service)
    half = " ".join(["word"] * 45)
    md = f"\n## Busy {{#busy}}\n\n```python\nx = 1\ny = 2\n```\n\n{half} [x](#L1) {half} [y](#L2) {half}.\n"
    await service.add(r["handle"], OPENING + md)
    await voiced(store.by_handle(r["handle"]))
    a = await service.add(r["handle"], "## Next\n\nMore.\n")
    assert "still_slide" not in codes(a["issues"])


async def test_finish_says_what_was_not_checked(service, store):
    r = await started(service)
    await service.add(r["handle"], OPENING)
    f = await service.finish(r["handle"], None)
    assert any(i["code"] == "not_checked" and "No player" in i["message"] for i in f["issues"])
    assert f["outline"][0] == {"id": "how-caching-works", "title": "How caching works", "start_s": 0.0}
    s = store.by_handle(r["handle"])
    q = s.subscribe()
    f = await service.finish(r["handle"], None)
    s.unsubscribe(q)
    assert not any("No player" in i["message"] for i in f["issues"])
