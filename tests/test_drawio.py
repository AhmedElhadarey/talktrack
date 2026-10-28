"""draw.io diagrams: reading plain and compressed files, pages and .drawio.svg exports, what the
cleaner keeps, parts and edges for step reveals, and cues."""

from __future__ import annotations

import base64
import re
import urllib.parse
import zlib
from pathlib import Path

from talktrack.kinds.drawio import clean_style, find, parse_drawio

FIXTURE = Path(__file__).parent / "fixtures" / "drawio" / "architecture.drawio"
OPEN = "# T\n\nOpening.\n\n"


def last(build, md: str, **kw):
    w, b = build(OPEN + md, **kw)
    b.check_all()
    return w.slides[-1], [i.code for i in b.issues], b


def compress(model_xml: str) -> str:
    """Pack a page the way draw.io does: deflate (raw) the URL-encoded XML, then base64."""
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = c.compress(urllib.parse.quote(model_xml, safe="~()*!.'").encode()) + c.flush()
    return base64.b64encode(data).decode()


def test_parts_edges_and_labels():
    v, problems = parse_drawio(FIXTURE.read_text(), {})
    assert not problems and v["page"] == "Overview"
    assert v["ids"] == ["client", "api", "db", "cloud", "store"]  # vertices on the layer, in source order
    assert v["edges"] == {"e1": ["client", "api"], "e2": ["api", "db"]}
    assert v["part_of"] == {"e1": "api", "e2": "db", "queue": "cloud", "worker": "cloud"}
    labels = {i["id"]: i["label"] for i in v["items"]}
    assert labels["api"] == "API server docs" and labels["db"] == "Postgres" and labels["e1"] == "HTTPS"


def test_links_scripts_and_wrappers_are_gone():
    v, _ = parse_drawio(FIXTURE.read_text(), {})
    xml = v["xml"]
    for bad in ("javascript", "example.com", "<a ", "UserObject", "tooltip", "link="):
        assert bad not in xml, bad
    assert 'id="db"' in xml and 'value="Postgres"' in xml and "&lt;b&gt;API&lt;/b&gt;" in xml and 'math="0"' in xml


def test_label_html_keeps_formatting_only():
    body = FIXTURE.read_text().replace(
        "&lt;b&gt;API&lt;/b&gt; server",
        "&lt;b onclick=&quot;x()&quot;&gt;API&lt;/b&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;img src=x onerror=y&gt; server",
    )
    v, _ = parse_drawio(body, {})
    api = re.search(r'<mxCell id="api" value="([^"]*)"', v["xml"]).group(1)
    assert api.startswith("&lt;b&gt;API&lt;/b&gt;") and "onclick" not in api and "script" not in api and "img" not in api


def test_styles_lose_outside_references():
    style = "shape=image;image=https://x/y.png;link=https://x;fontSource=https%3A%2F%2Ffonts;rounded=1;tooltip=hi"
    assert clean_style(style) == "shape=image;rounded=1"
    assert clean_style("shape=image;image=data:image/png,iVBORw0;html=1") == "shape=image;image=data:image/png,iVBORw0;html=1"


def test_compressed_pages_and_page_choice():
    text = FIXTURE.read_text()
    model = re.search(r"<mxGraphModel.*?</mxGraphModel>", text, re.S).group(0)
    packed = re.sub(r"<mxGraphModel.*?</mxGraphModel>", compress(model), text, count=1, flags=re.S)
    v, problems = parse_drawio(packed, {})
    assert not problems and v["ids"] == ["client", "api", "db", "cloud", "store"]
    v, problems = parse_drawio(text, {"page": "2"})
    assert not problems and v["page"] == "Second page" and v["ids"] == ["only"]
    v, problems = parse_drawio(text, {"page": "Second page"})
    assert v["ids"] == ["only"]
    _, problems = parse_drawio(text, {"page": "Nope"})
    assert "1 `Overview`, 2 `Second page`" in problems[0]
    _, problems = parse_drawio(text.replace(model, "bm90IGRlZmxhdGU="), {})
    assert "could not be decompressed" in problems[0]


def test_drawio_svg_exports_carry_their_diagram():
    content = FIXTURE.read_text().replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" content="{content}"><rect/></svg>'
    v, problems = parse_drawio(svg, {})
    assert not problems and v["ids"][:2] == ["client", "api"]
    _, problems = parse_drawio('<svg viewBox="0 0 1 1"><rect/></svg>', {})
    assert "```svg" in problems[0]


def test_bad_files_are_reported():
    assert "not a draw.io file" in parse_drawio("<html><body/></html>", {})[1][0]
    assert "well-formed" in parse_drawio("<mxfile><diagram>", {})[1][0]
    assert "entities" in parse_drawio('<!DOCTYPE x [<!ENTITY a "b">]><mxfile/>', {})[1][0]
    assert "no pages" in parse_drawio("<mxfile/>", {})[1][0]


def test_find_by_id_label_or_slug():
    v, _ = parse_drawio(FIXTURE.read_text(), {})
    assert [find(v, t) for t in ("queue", "Postgres", "api-server-docs", "object-store", "Nothing")] == [
        "queue",
        "db",
        "api",
        "store",
        None,
    ]


def test_cues_reveal_order_and_regions(build, project):
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "arch.drawio").write_text(FIXTURE.read_text())
    s, codes, _ = last(
        build,
        "## A {#a .build}\n\n```drawio file=docs/arch.drawio\n```\n\n"
        "The [browser](#client) calls [the API](#api) over [HTTPS](#e1).\n\n"
        "Data sits in [Postgres](#Postgres), and [the queue](#queue) feeds [a store](#@70,50,30,50). "
        'The [label](# "Object store") names it.\n',
        root=project,
    )
    cues = [c for say in s.says for c in say.cues]
    assert s.kind == "drawio" and [c["kind"] for c in cues] == ["item", "item", "item", "item", "item", "region", "text"]
    assert cues[2]["id"] == "e1" and cues[2]["part"] == "api" and cues[4]["part"] == "cloud" and cues[3]["id"] == "db"
    assert not [c for c in codes if c.startswith("cue") or c == "build_cue_order"], codes


def test_missing_cell_and_missing_file(build, project):
    (project / "arch.drawio").write_text(FIXTURE.read_text())
    _, codes, b = last(build, "## A\n\n```drawio file=arch.drawio\n```\n\nThe [cache](#cache).\n", root=project)
    issue = next(i for i in b.issues if i.code == "cue_no_target")
    assert "no cell `cache`" in issue and "client, api, db" in issue
    _, codes, _ = last(build, "## A\n\n```drawio file=missing.drawio\n```\n\nNothing.\n", root=project)
    assert "source_error" in codes
