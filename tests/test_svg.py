"""Drawings: what the SVG cleaner keeps and removes, parts and nested parts, cues, files on disk."""

from __future__ import annotations

from showntell.kinds.svg import find, parse_svg

OPEN = "# T\n\nOpening.\n\n"
DRAWING = """<svg viewBox="0 0 400 200">
  <g id="sun"><title>The sun</title><circle cx="40" cy="40" r="30" fill="#ffd166"/><circle id="core" cx="40" cy="40" r="8"/></g>
  <path id="beam" d="M70 40 L300 150" stroke="#4f8cff" fill="none"/>
  <text id="eye" x="10" y="190" fill="var(--fg)">Your eye</text>
</svg>"""


def last(build, md: str, **kw):
    w, b = build(OPEN + md, **kw)
    b.check_all()
    return w.slides[-1], [i.code for i in b.issues], b


def test_parts_are_the_outermost_ids_in_source_order():
    v, problems = parse_svg(DRAWING, {})
    assert not problems and v["ids"] == ["sun", "beam", "eye"] and v["part_of"] == {"core": "sun"}
    assert {i["id"]: i["label"] for i in v["items"]} == {"sun": "The sun", "core": "", "beam": "", "eye": "Your eye"}
    assert v["box"] == [0, 0, 400, 200] and 'preserveAspectRatio="xMidYMid meet"' in v["svg"]


def test_nothing_that_runs_or_fetches_survives():
    src = """<svg viewBox="0 0 10 10" onload="x()">
      <script>alert(1)</script>
      <g id="a" onclick="x()"><rect width="5" height="5" fill="url(http://evil/#p)" style="fill: url(http://x); stroke: red"/></g>
      <a href="javascript:alert(1)"><text id="t">kept</text></a>
      <foreignObject><div xmlns="http://www.w3.org/1999/xhtml" id="h">html</div></foreignObject>
      <image href="http://evil/i.png"/><use href="http://evil/s.svg#x"/><use href="#a"/>
      <animate attributeName="fill" to="red"/><set attributeName="href" to="javascript:x"/>
      <style>@import url(http://evil/s.css); .ok { fill: red }</style>
      <style>.ok { fill: red } .bad { fill: url(http://evil) }</style>
    </svg>"""
    v, _ = parse_svg(src, {})
    svg = v["svg"].lower()
    for bad in ("script", "onload", "onclick", "javascript", "evil", "foreignobject", "<animate", "<set", "@import", "<a "):
        assert bad not in svg, bad
    assert "kept" in svg and 'href="#a"' in svg and "stroke: red" in svg and ".ok { fill: red }" in svg
    assert v["ids"] == ["a", "t"]  # the html element's id went with it
    assert {"script", "onclick=", "a", "foreignObject", "animate", "set"} <= set(v["removed"])


def test_entities_are_refused_and_the_doctype_dropped():
    _, problems = parse_svg('<!DOCTYPE svg [<!ENTITY x "boom">]><svg viewBox="0 0 1 1"><text>&x;</text></svg>', {})
    assert problems and "entities" in problems[0]
    v, problems = parse_svg('<?xml version="1.0"?><!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd"><svg viewBox="0 0 1 1"/>', {})
    assert not problems and v["svg"].startswith("<svg")


def test_size_comes_from_the_viewbox_or_width_and_height():
    v, problems = parse_svg('<svg width="300" height="150px"><rect/></svg>', {})
    assert not problems and v["box"] == [0, 0, 300, 150] and 'viewBox="0 0 300 150"' in v["svg"] and 'width="300"' not in v["svg"]
    _, problems = parse_svg("<svg><rect/></svg>", {})
    assert "viewBox" in problems[0]
    _, problems = parse_svg("<svg viewBox='0 0 1 1'><rect></svg>", {})
    assert "well-formed" in problems[0]


def test_embedded_fonts_and_images_are_kept_and_xlink_becomes_href():
    font = "data:font/woff2;base64,d09GMgABAAAA"
    src = f"""<svg xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">
      <style>@font-face {{ font-family: "Hand"; src: url({font}); }} @font-face {{ font-family: "Web"; src: url(https://x/f.woff2); }}</style>
      <image xlink:href="data:image/png;base64,iVBORw0KGgo="/><use xlink:href="#p"/><path id="p" d="M0 0"/></svg>"""
    v, _ = parse_svg(src, {})
    assert font in v["svg"] and "https://x" not in v["svg"] and 'href="data:image/png' in v["svg"] and '<use href="#p">' in v["svg"]


def test_find_by_id_title_text_or_slug():
    v, _ = parse_svg(DRAWING, {})
    assert [find(v, t) for t in ("core", "The sun", "your-eye", "nothing")] == ["core", "sun", "eye", None]


def test_cues_point_at_parts_nested_parts_regions_and_text(build):
    s, codes, _ = last(build, f"## D {{#d .build}}\n\n```svg\n{DRAWING}\n```\n\n"
                              "The [sun](#sun) has [a core](#core). Then [the beam](#beam) at [the corner](#@0,0,20,20) "
                              'reaches [you](# "Your eye").\n')
    cues = s.says[0].cues
    assert s.kind == "svg" and [c["kind"] for c in cues] == ["item", "item", "item", "region", "text"]
    assert cues[1]["id"] == "core" and cues[1]["part"] == "sun" and "part" not in cues[0]
    assert not [c for c in codes if c.startswith("cue") or c == "build_cue_order"]


def test_unknown_part_lists_the_ids(build):
    _, codes, b = last(build, f"## D\n\n```svg\n{DRAWING}\n```\n\nThe [moon](#moon).\n")
    issue = next(i for i in b.issues if i.code == "cue_no_target")
    assert "no element `moon`" in issue and "sun, beam, eye" in issue


def test_removed_parts_and_bad_drawings_are_reported(build):
    _, codes, b = last(build, '## D\n\n```svg\n<svg viewBox="0 0 1 1"><script>x</script><rect id="r"/></svg>\n```\n\nThe [box](#r).\n')
    assert "svg_removed" in codes and "script" in next(i for i in b.issues if i.code == "svg_removed")
    _, codes, _ = last(build, "## D\n\n```svg\n<svg><rect/></svg>\n```\n\nNothing.\n")
    assert "block_error" in codes


def test_drawings_are_read_from_disk(build, project):
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "flow.svg").write_text(DRAWING)
    s, codes, _ = last(build, "## D\n\n```svg file=docs/flow.svg\n```\n\nThe [beam](#beam).\n", root=project)
    assert s.kind == "svg" and s.visual["ids"] == ["sun", "beam", "eye"] and "source_error" not in codes
    _, codes, _ = last(build, "## D\n\n```svg file=../outside.svg\n```\n\nNo.\n", root=project)
    assert "source_error" in codes


def test_step_drawings_are_versions_of_one_slide(build):
    one = '<svg viewBox="0 0 100 100"><circle id="moon" cx="80" cy="50" r="5"/></svg>'
    two = '<svg viewBox="0 0 100 100"><circle id="moon" cx="50" cy="20" r="5"/><text id="note">later</text></svg>'
    s, codes, _ = last(build, f"## O {{#o}}\n\n```svg step\n{one}\n```\n```svg step\n{two}\n```\n\n"
                              "The [moon](#moon) moves [a week later](#step-2), see [the note](#note).\n")
    assert s.kind == "svg" and len(s.visual["steps"]) == 2 and s.visual["steps"][1]["ids"] == ["moon", "note"]
    assert [c["kind"] for c in s.says[0].cues] == ["item", "step", "item"] and s.says[0].cues[1]["n"] == 2
    assert "extra_visual" not in codes and not [c for c in codes if c.startswith("cue")]
    _, codes, b = last(build, f"## O\n\n```svg\n{one}\n```\n\nNo [later version](#step-2).\n")
    assert "cue_no_target" in codes


def test_sketch_is_a_flag_on_the_fence(build):
    s, _, _ = last(build, '## S\n\n```svg sketch\n<svg viewBox="0 0 10 10"><rect id="r" width="5" height="5"/></svg>\n```\n\nA [box](#r).\n')
    assert s.visual.get("sketch") is True
