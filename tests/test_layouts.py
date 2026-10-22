"""Layouts, icons and looks: `.side`, `.pair` and `.full` slides, Lucide icons wherever text can lead
with one, and the findings that ask for varied slides."""

from __future__ import annotations

from showntell import icons
from showntell.kinds.svg import parse_svg
from showntell.model import public_slide


def codes(b) -> list[str]:
    return [i.code for i in b.issues]


# -- icons -----------------------------------------------------------------------------------------


def test_icons_lead_list_items_and_are_copied_into_the_slide(build):
    w, b = build(
        "# T\n\nA.\n\n## Parts {.cards}\n\n- :database: **Store**: keeps rows\n- **Plain**: no icon\n\nThe [store](#store) keeps rows.\n"
    )
    s = w.slides[-1]
    assert [it.get("icon") for it in s.visual["items"]] == ["database", None]
    assert s.visual["items"][0]["title"] == "Store" and s.visual["items"][0]["id"] == "store"
    assert s.icons == {"database": icons.get("database")} and "<ellipse" in s.icons["database"]
    assert public_slide(s)["icons"] == s.icons
    assert "icon_unknown" not in codes(b)


def test_an_unknown_icon_is_reported_with_close_names_and_left_out(build):
    w, b = build("# T\n\nA.\n\n## Parts\n\n- :databse: **Store**: keeps rows\n\nThe [store](#store) keeps rows.\n")
    s = w.slides[-1]
    assert "icon" not in s.visual["items"][0] and not s.icons
    issue = next(i for i in b.issues if i.code == "icon_unknown")
    assert "`databse`" in issue.message and "database" in issue.message


def test_icons_on_headings_takeaways_and_numbers(build):
    w, b = build(
        "# Caching {icon=database-zap}\n\nA.\n\n## Faster\n> :zap: Reads skip the database\n\n"
        "```stat\n:clock: p95 latency: 420 ms -> 180 ms\nerrors: 2% -> 1%\n```\n\nThe [latency](#p95-latency) halves.\n"
    )
    title, s = w.slides[0], w.slides[-1]
    assert title.icon == "database-zap" and "database-zap" in title.icons
    assert s.shown == ":zap: Reads skip the database" and "zap" in s.icons
    assert [it.get("icon") for it in s.visual["items"]] == ["clock", None]
    assert s.visual["items"][0]["label"] == "p95 latency" and s.visual["ids"][0] == "p95-latency"
    assert "clock" in s.icons and not [c for c in codes(b) if c in ("icon_unknown", "block_error", "cue_no_target")]


def test_an_unknown_icon_in_a_takeaway_is_dropped_from_the_shown_text(build):
    w, b = build("# T\n\nA.\n\n## S\n> :no-such-icon-here: The point\n\nSaid.\n")
    assert w.slides[-1].shown.strip() == "The point"
    assert "icon_unknown" in codes(b)


def test_icons_in_drawings_become_groups_that_cues_reach():
    v, problems = parse_svg(
        '<svg viewBox="0 0 200 100"><use id="db" href="icon:database" x="10" y="20" width="48" stroke="var(--c2)" transform="rotate(5)">'
        '<title>Database</title></use><use href="icon:nope-nope"/></svg>',
        {},
    )
    assert v["ids"] == ["db"] and v["items"][0]["label"] == "Database"
    assert 'transform="rotate(5) translate(10 20) scale(2)"' in v["svg"] and 'stroke="var(--c2)"' in v["svg"]
    assert "<ellipse" in v["svg"] and "icon:" not in v["svg"]
    assert any("no icon `nope-nope`" in p for p in problems)


def test_suggestions_prefer_spelling_then_tags():
    assert icons.suggest("databse")[0] == "database"
    assert "gauge" in icons.suggest("speed")
    assert icons.split_leading(":zap: fast") == ("zap", "fast") and icons.split_leading("plain: text") == ("", "plain: text")


# -- pairs -----------------------------------------------------------------------------------------


PAIR = (
    "# T\n\nA.\n\n## Code and flow {#p .pair}\n\n```python\ndef get(key):\n    return cache[key]\n```\n\n"
    "```mermaid\nflowchart LR\n  req[Request] --> hit{In cache?}\n```\n\n"
)


def test_a_pair_keeps_both_visuals_and_cues_reach_either(build):
    w, b = build(PAIR + "The [lookup](#L2) is one line, and [the check](#hit) decides.\n")
    s = w.slides[-1]
    assert s.kind == "code" and s.pair["kind"] == "diagram" and len(w.slides) == 2
    cues = s.says[0].cues
    assert cues[0]["kind"] == "lines" and "pane" not in cues[0]
    assert cues[1]["kind"] == "node" and cues[1]["id"] == "hit" and cues[1]["pane"] == 1
    assert "extra_visual" not in codes(b) and public_slide(s)["pair"]["kind"] == "diagram"
    assert not [i for i in b.issues if i.severity == "error"], b.issues


def test_a_cue_neither_pane_has_is_reported_against_the_first(build):
    _, b = build(PAIR + "The [nothing](#nowhere) is here.\n")
    assert [i for i in b.issues if i.code == "cue_wrong_kind" and "shows a code" in i.message]


def test_a_third_visual_on_a_pair_gets_its_own_slide(build):
    w, b = build(PAIR + "```python\nx = 1\n```\n\nSaid.\n")
    assert len(w.slides) == 3 and "extra_visual" in codes(b)


def test_without_pair_a_second_visual_still_moves_on(build):
    w, b = build("# T\n\nA.\n\n## S\n\n```python\nx = 1\n```\n\n```python\ny = 2\n```\n\nSaid.\n")
    assert len(w.slides) == 3 and not w.slides[1].pair


# -- layout findings -------------------------------------------------------------------------------


def test_layout_classes_with_nothing_to_lay_out(build):
    _, b = build(
        "# T\n\nA.\n\n## Side {.side}\n\n```python\nx = 1\n```\n\nThe [line](#L1) sets x.\n\n"
        "## Pair {.pair .build}\n\n```python\ny = 2\n```\n\nThe [line](#L1) sets y.\n\n"
        "## Morph {.morph}\n\n```python\nz = 3\n```\n\nThe [line](#L1) sets z.\n"
    )
    msgs = [i.message for i in b.issues if i.code == "layout_unused"]
    assert any("`.side`" in m for m in msgs) and any("`.pair` shows two" in m for m in msgs)
    assert any("`.build` does not reveal a pair" in m for m in msgs) and any("`.morph`" in m for m in msgs)


def test_two_alike_slides_in_a_row_are_reported_once(build):
    md = "# T\n\nA.\n\n" + "".join(f"## S{i}\n> point {i}\n\n```python\nx = {i}\n```\n\nThe [line](#L1) sets it.\n\n" for i in range(4))
    _, b = build(md)
    assert codes(b).count("same_look") == 1


def test_a_different_layout_breaks_the_sameness(build):
    _, b = build(
        "# T\n\nA.\n\n## A\n> one\n\n```python\nx = 1\n```\n\nThe [line](#L1) sets it.\n\n"
        "## B {.side}\n> two\n\n```python\ny = 1\n```\n\nThe [line](#L1) sets it.\n"
    )
    assert "same_look" not in codes(b)


def test_dense_slides_without_a_takeaway_are_reported_once(build):
    md = "# T\n\nA.\n\n" + "".join(f"## S{i}\n\n| a | b |\n|---|---|\n| {i} | x |\n\nThe [row](#row-1) says it.\n\n" for i in range(3))
    _, b = build(md)
    assert codes(b).count("no_takeaway") == 1
    _, b = build(
        "# T\n\nA.\n\n" + "".join(f"## S{i}\n> the point\n\n```python\nx = {i}\n```\n\nThe [line](#L1) sets it.\n\n" for i in range(3))
    )
    assert "no_takeaway" not in codes(b)


def test_each_slide_has_a_look_for_the_outline(build):
    from showntell.checks import look

    w, _ = build(
        PAIR
        + "The [lookup](#L2) is one line.\n\n## Parts {.cards .side}\n> :zap: Fast\n\n- :database: **Store**: rows\n\nThe [store](#store) keeps rows.\n"
    )
    assert [look(s) for s in w.slides] == ["title", "code+diagram", "cards side takeaway icons"]


def test_alike_runs_skip_drawings_statements_and_morphs(build):
    from showntell.checks import alike_runs

    code = "```python\nx = 1\n```\n\nThe [line](#L1) sets it.\n\n"
    w, _ = build(
        "# T\n\nA.\n\n"
        + f"## A\n\n{code}## B\n\n{code}## C {{.side}}\n> point\n\n{code}## D {{.side}}\n> point\n\n{code}"
        + "## Say\n> A statement.\n\nSaid.\n\n"
        + f"## E\n\n{code}"
    )
    assert [[s.title for s in run] for run in alike_runs(w.slides)] == [["A", "B"], ["C", "D"]]
