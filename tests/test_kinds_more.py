"""Code that evolves, long code, edge paths, pointer styles, regions and framed images, numbers,
exchanges, chart notes, chapter cards, layer stacks and checklists."""

from __future__ import annotations

import pytest

from showntell.format import parse_item, parse_narration
from showntell.kinds import parse_chart, parse_chat, parse_stat

OPEN = "# T\n\nOpening.\n\n"


def last(build, md: str, **kw):
    w, b = build(OPEN + md, **kw)
    b.check_all()
    return w.slides[-1], [i.code for i in b.issues], b


# -- pointer styles ------------------------------------------------------------------------------


def test_cue_styles_are_parsed_and_unknown_ones_dropped():
    n = parse_narration("See [the guard](#L12){.circle .zoom .sparkle} and [b](#c){.underline}.")
    assert [c.style for c in n.cues] == ["circle zoom", "underline"] and n.display == "See the guard and b."


def test_cue_style_reaches_the_slide(build):
    s, codes, _ = last(build, "## C\n\n```python\nx = 1\n```\n\nThe [value](#L1){.highlight} is one.\n")
    assert s.says[0].cues[0]["style"] == "highlight" and "cue_no_target" not in codes


# -- code that evolves, long code --------------------------------------------------------------------


STEPS = "## E {#e}\n\n```python step\ndef f(xs):\n    t = 0\n    for x in xs:\n        t += x\n    return t\n```\n```python step\ndef f(xs):\n    return sum(xs)\n```\n\n"


def test_steps_build_one_slide_and_line_cues_follow_the_step(build):
    s, codes, _ = last(build, STEPS + 'The loop [adds](#L4). Then [one call](#step-2) and [sum](#L2 "sum").\n')
    assert s.kind == "code" and len(s.visual["steps"]) == 2 and "extra_visual" not in codes
    cues = s.says[0].cues
    assert [(c["kind"], c.get("step"), c.get("n")) for c in cues] == [("lines", 1, None), ("step", None, 2), ("lines", 2, None)]
    assert "cue_text_missing" not in codes  # `sum` is checked against step 2, not step 1


def test_step_cue_past_the_last_step(build):
    _, codes, _ = last(build, STEPS + "Then [more](#step-3).\n")
    assert "cue_no_target" in codes


def test_scroll_lifts_the_size_limit_but_not_forever(build):
    code = "\n".join(f"x{i} = {i}" for i in range(60))
    _, codes, _ = last(build, f"## L\n\n```python scroll\n{code}\n```\n\nSee [the end](#L60).\n")
    assert "code_too_tall" not in codes
    _, codes, _ = last(build, f"## L\n\n```python\n{code}\n```\n\nSee [the end](#L60).\n")
    assert "code_too_tall" in codes
    huge = "\n".join(f"x{i} = {i}" for i in range(450))
    _, codes, _ = last(build, f"## L\n\n```python scroll\n{huge}\n```\n\nSee [one](#L1).\n")
    assert "code_too_tall" in codes


# -- packets along edges -------------------------------------------------------------------------------


FLOW = "## P {#p}\n\n```mermaid\nflowchart LR\n  c[Client] --> g[Gateway] --> s[Service]\n  g -->|cached| r[(Redis)]\n```\n\n"


@pytest.mark.parametrize("target", ["c->g", "c->g->s", "g->r"])
def test_edge_paths_resolve(build, target):
    s, codes, _ = last(build, FLOW + f"A request [goes through]({'#' + target}).\n")
    assert s.says[0].cues[0]["kind"] == "edge" and s.says[0].cues[0]["path"] == target.split("->")
    assert "cue_no_target" not in codes


@pytest.mark.parametrize("target, why", [("c->s", "no edge c->s"), ("s->g", "no edge s->g"), ("c->x", "no node `x`")])
def test_missing_edges_and_nodes_are_reported(build, target, why):
    _, _, b = last(build, FLOW + f"It [goes]({'#' + target}).\n")
    assert any(i.code == "cue_no_target" and why in i for i in b.issues)


def test_edge_paths_need_a_flowchart(build):
    _, codes, _ = last(build, "## L\n\n- **A**: a {#a}\n- **B**: b {#b}\n\nFrom [a to b](#a->b).\n")
    assert "cue_wrong_kind" in codes


# -- images: regions and frames ------------------------------------------------------------------


def test_region_cues_and_frames(build, project):
    md = '## S\n\n![The shop](docs/timeline.png){frame=browser url="shop.example.com"}\n\nThe [search box](#@5,17,54,8){.zoom} is here.\n'
    s, codes, _ = last(build, md, root=project)
    assert s.visual["frame"] == "browser" and s.visual["url"] == "shop.example.com"
    assert s.says[0].cues[0]["kind"] == "region" and s.says[0].cues[0]["box"] == [5, 17, 54, 8]
    assert s.says[0].cues[0]["style"] == "zoom"
    assert not {"cue_no_target", "cue_wrong_kind", "image_error"} & set(codes)


@pytest.mark.parametrize("spec", ["@5,17,54", "@90,10,20,10", "@-1,0,10,10", "@a,b,c,d"])
def test_bad_regions_are_reported(build, project, spec):
    _, codes, _ = last(build, f"## S\n\n![The shop](docs/timeline.png)\n\nThe [box](#{spec}).\n", root=project)
    assert "cue_no_target" in codes


def test_unknown_frame_is_reported(build, project):
    _, codes, _ = last(build, "## S\n\n![The shop](docs/timeline.png){frame=tablet}\n\nWords.\n", root=project)
    assert "image_error" in codes


# -- numbers, exchanges, chart notes ----------------------------------------------------------------


def test_stat_values_units_and_problems():
    v, problems = parse_stat("p95 latency: 420 ms -> 180 ms\nerrors: 2.1% → 0.35%\nusers: 12,400\nbad: lots", {})
    assert [i["id"] for i in v["items"]] == ["p95-latency", "errors", "users"]
    first = v["items"][0]
    assert (first["before"], first["after"], first["unit"]) == (420, 180, "ms") and v["items"][1]["decimals"] == 2
    assert v["items"][2]["after"] == 12400 and "before" not in v["items"][2]
    assert any("`bad`" in p for p in problems)


def test_stat_slide_cues(build):
    s, codes, _ = last(build, "## N\n\n```stat\np95 latency: 420 -> 180\n```\n\nThe [p95 latency](#p95-latency) halved.\n")
    assert s.kind == "stat" and s.says[0].cues[0]["id"] == "p95-latency" and "cue_no_target" not in codes


def test_chat_messages_sides_and_continuations():
    v, problems = parse_chat("assistant: Hi.\nuser: Undo?\nassistant: Use reset.\n  It keeps changes.", {})
    assert not problems and [m["side"] for m in v["items"]] == ["left", "right", "left"]
    assert v["items"][2]["text"] == "Use reset.\nIt keeps changes." and v["ids"] == ["m1", "m2", "m3"]
    v, _ = parse_chat("alice: one\nbob: two", {})
    assert [m["side"] for m in v["items"]] == ["left", "right"]


def test_chat_slide_cues_and_size(build):
    s, codes, _ = last(build, "## C {#c .build}\n\n```chat\nuser: Hi\nbot: Hello\n```\n\nThe [question](#m1), then [the answer](#m2).\n")
    assert s.kind == "chat" and [c["id"] for c in s.says[0].cues] == ["m1", "m2"]
    many = "\n".join(f"u{i % 2}: message {i}" for i in range(12))
    _, codes, _ = last(build, f"## C\n\n```chat\n{many}\n```\n\nA lot.\n")
    assert "block_too_big" in codes


def test_chart_notes_are_targets():
    v, problems = parse_chart("x: 1, 2, 4\nfast: 1, 2, 3\nslow: 2, 5, 9\nnote slow/4: grows\nnote 2: both", {"flags": ["line"]})
    assert [(n["id"], n["x"], n["series"]) for n in v["notes"]] == [("note-1", 2, "slow"), ("note-2", 1, None)] and not problems
    v, problems = parse_chart("read: 4\nwrite: 6\nnote write: slowest\nnote delete: x", {"flags": ["bar"]})
    assert v["notes"][0]["bar"] == "write" and any("no bar `delete`" in p for p in problems)


def test_chart_note_cue(build):
    s, codes, _ = last(build, "## C\n\n```chart bar\nread: 4\nwrite: 6\nnote write: slowest\n```\n\nThe [slowest](#note-1) one.\n")
    assert s.says[0].cues[0]["id"] == "note-1" and "cue_no_target" not in codes


# -- chapters, stacks, checklists -------------------------------------------------------------------


def test_chapters_are_numbered_and_need_no_narration(build):
    w, b = build(OPEN + "## Part A {.chapter}\n\n## Body\n\nWords.\n\n## Part B {.chapter}\n> The rest\n\n## More\n\nWords.\n")
    b.check_all()
    chapters = [s for s in w.slides if s.kind == "chapter"]
    assert [c.visual["n"] for c in chapters] == [1, 2] and chapters[1].shown == "The rest"
    assert not any(i.code == "no_narration" for i in b.issues)


def test_checklist_items_and_style(build):
    assert parse_item("[x] **Tests**: pass")["check"] is True and parse_item("[ ] Docs")["check"] is False
    assert "check" not in parse_item("Plain")
    s, _, _ = last(build, "## R\n\n- [x] **Tests**: pass {#tests}\n- [ ] **Docs**: later {#docs}\n\nThe [tests](#tests) pass.\n")
    assert s.visual["style"] == "checklist" and [i["check"] for i in s.visual["items"]] == [True, False]


def test_stack_style(build):
    s, _, _ = last(build, "## L {.stack}\n\n- **App**: code\n- **TCP**: transport\n\nDown [the app](#app).\n")
    assert s.visual["style"] == "stack"


def test_stat_units_may_be_any_short_text():
    v, problems = parse_stat("Lens area: 1 cm² -> 20 cm²\nDistance: 147.1 million km\nTemperature: 21.5 °C", {})
    assert not problems and [i["unit"] for i in v["items"]] == ["cm²", "million km", "°C"]
    assert v["items"][0]["before"] == 1 and v["items"][1]["after"] == 147.1


QUIZ = "## Which call retries? {.quiz}\n\n- [ ] `fetch`\n- [x] `send`\n- [ ] `close`\n\n"


def test_quiz_choices_are_lettered_and_one_is_right(build):
    s, codes, _ = last(build, QUIZ + "Which one retries?\n\n{pause=3}\n\nIt's [send](#b).\n")
    assert s.visual["style"] == "quiz"
    assert [i["id"] for i in s.visual["items"]] == ["a", "b", "c"]
    assert [bool(i.get("check")) for i in s.visual["items"]] == [False, True, False]
    assert not [c for c in codes if c.startswith("quiz_")]


def test_quiz_without_one_right_answer_is_reported(build):
    _, codes, _ = last(build, "## Q {.quiz}\n\n- [ ] one\n- [ ] two\n\nWhich? Neither, it turns out: [one](#a).\n\nOr [two](#b).\n")
    assert "quiz_shape" in codes


def test_quiz_answer_given_with_the_question_is_reported(build):
    _, _, b = last(build, QUIZ + "Is it [send](#b)?\n")
    assert any(i.code == "quiz_reveal" and "first paragraph" in i for i in b.issues)


def test_quiz_answer_never_pointed_at_is_reported(build):
    _, _, b = last(build, QUIZ + "Which one retries?\n\nThink about it.\n")
    assert any(i.code == "quiz_reveal" and "never points" in i for i in b.issues)
