"""Single-page export: one HTML file with the player, the walkthrough, its narration and only the
libraries it needs, playing from disk with the network blocked."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest

from showntell.export import LIBRARIES, _katex_css, _library, _modules, _script_json, _swap

# -- building the page ----------------------------------------------------------------------------


def test_modules_import_each_other_by_bare_specifiers():
    mods = _modules()
    assert "showntell/js/main.js" in mods and "showntell/js/kinds/chart.js" in mods
    for key, uri in mods.items():
        code = base64.b64decode(uri.split(",", 1)[1]).decode()
        relative = [ln for ln in code.splitlines() if ln.startswith("import") and ('"./' in ln or '"../' in ln)]
        assert not relative, (key, relative)
        for spec in __import__("re").findall(r'from "(showntell/js/[^"]+)"', code):
            assert spec in mods, (key, spec)


def test_script_json_cannot_close_its_script_element():
    s = _script_json({"code": "x = '</script><!-- <script>'"})
    assert "</script" not in s and "<!--" not in s and json.loads(s)["code"] == "x = '</script><!-- <script>'"


def test_katex_fonts_travel_inside_the_stylesheet():
    css = _katex_css()
    assert "url(fonts/" not in css and css.count("data:font/woff2;base64,") >= 20 and ".ttf" not in css


def test_large_libraries_are_gzipped_and_highlightjs_is_not():
    import gzip

    from showntell.export import STATIC

    m = _library("vendor/mermaid.min.js")
    assert gzip.decompress(base64.b64decode(m["gzip"])) == (STATIC / "vendor" / "mermaid.min.js").read_bytes()
    assert len(m["gzip"]) < 0.5 * (STATIC / "vendor" / "mermaid.min.js").stat().st_size
    assert isinstance(_library("vendor/hljs/highlight.min.js"), str)


def test_libraries_follow_the_slide_kinds():
    assert LIBRARIES["diagram"] == ["vendor/mermaid.min.js"] and "list" not in LIBRARIES


def test_a_changed_template_is_caught():
    with pytest.raises(RuntimeError, match="player.html changed"):
        _swap("<html></html>", "<script>", "x")


# -- opened from disk ------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _voiced(data, markdown: str, root: str) -> str:
    """Write a walkthrough with a fake-speech daemon and wait until it is voiced and saved."""
    env = {**os.environ, "SHOWNTELL_DATA": str(data), "SHOWNTELL_SPEECH": "fake", "SHOWNTELL_FINISH_CHECK": "0"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "showntell", "serve", "--port", str(_free_port()), "--speech", "fake"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        lock, deadline = None, time.time() + 20
        while time.time() < deadline:
            try:
                lock = json.loads((data / "daemon.json").read_text())
                if httpx.get(f"http://127.0.0.1:{lock['port']}/health", timeout=1).status_code == 200:
                    break
            except (OSError, ValueError, httpx.HTTPError):
                pass
            time.sleep(0.2)
        assert lock, "daemon did not start"
        h = {"Authorization": f"Bearer {lock['token']}"}

        def call(m, **kw):
            r = httpx.post(f"http://127.0.0.1:{lock['port']}/api/rpc/{m}", json=kw, headers=h, timeout=30)
            r.raise_for_status()
            return r.json()["result"]

        r = call("start", title="Every slide kind", frame="landscape", voice="", speed=1.0, project_dir=root)
        call("add", handle=r["handle"], markdown=markdown)
        for _ in range(100):
            if not call("finish", handle=r["handle"], questions=[])["pending"]:
                break
            time.sleep(0.1)
        wid = r["handle"].split(".")[0]
        state = data / "walkthroughs" / wid / "state.json"
        for _ in range(100):  # state is saved a moment after the last change
            if state.is_file():
                st = json.loads(state.read_text())
                if st["status"] == "finished" and all(x["status"] == "ready" for s in st["slides"] for x in s["says"] if x.get("text")):
                    break
            time.sleep(0.1)
        return wid
    finally:
        proc.terminate()
        proc.wait(10)


@pytest.mark.browser
def test_one_page_plays_every_kind_offline(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from test_player_browser import DOC, KINDS, ROOT

    from showntell.export import export_html

    data = tmp_path / "data"
    wid = _voiced(data, DOC, str(ROOT))
    page_file = export_html(data, wid, tmp_path / "walk.html")
    html = page_file.read_text()
    assert "katex" in html and "mermaid" in html and 'src="static' not in html
    url = page_file.as_uri()
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        except Exception as e:  # browsers not installed
            pytest.skip(f"Chromium unavailable: {e}")
        page = b.new_page(viewport={"width": 1280, "height": 800})
        other, errors = [], []

        def only_the_file(route):
            if route.request.url.split("?")[0] == url:
                return route.continue_()
            other.append(route.request.url)
            return route.abort()

        page.route("**/*", only_the_file)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.wait_for_function("!document.querySelector('#big-play').disabled", timeout=15000)
        page.click("#big-play")
        page.wait_for_function("window.__showntell.starts.length > 1", timeout=15000)  # narration plays from blob: URLs
        # Every slide, drawn from what the page carries.
        page.goto(url + "?capture=1")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        dur = page.evaluate("window.__showntell.duration()")
        seen = {}
        for k in range(80):
            page.evaluate("(t) => window.__showntell.seek(t)", dur * k / 80)
            kind = page.evaluate("[...document.querySelector('.slide').classList].find((c) => c.startsWith('k-')).slice(2)")
            if kind == "diagram":
                seen[kind] = page.evaluate("document.querySelectorAll('.diagram svg g.node').length")
            elif kind == "math":
                seen[kind] = page.evaluate("document.querySelectorAll('.katex').length")
            elif kind == "code":
                seen[kind] = page.evaluate("document.querySelectorAll('.hljs-keyword, .hljs-string').length")
            elif kind == "image":
                seen[kind] = page.evaluate("document.querySelector('.image img').naturalWidth")
            else:
                seen.setdefault(kind, 1)
        assert set(seen) == set(KINDS), set(KINDS) - set(seen)
        assert all(seen[k] > 0 for k in ("diagram", "math", "code", "image")), seen
        assert not other, other
        assert not errors and not page.evaluate("window.__showntell.renderErrors"), errors
        b.close()


@pytest.mark.browser
def test_a_page_without_diagrams_leaves_the_libraries_out(tmp_path):
    from showntell.export import export_html

    data = tmp_path / "data"
    wid = _voiced(data, "# Plain\n\nJust words.\n\n## List {#l}\n\n- **A**: one\n- **B**: two\n\nThe [first](#a).\n", str(tmp_path))
    html = export_html(data, wid, tmp_path / "plain.html").read_text()
    files = json.loads(html.split('<script type="application/json" id="showntell-files">', 1)[1].split("</script>", 1)[0])
    assert files == {} and "mermaid" not in html and len(html) < 400_000


@pytest.mark.browser
def test_a_hand_drawn_drawing_carries_its_library_and_font(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from showntell.export import export_html

    md = (
        '# Sketch\n\nOpening words.\n\n## Box {#box}\n\n```svg sketch\n<svg viewBox="0 0 1150 560">'
        '<rect id="r" x="100" y="100" width="400" height="200" fill="#4f8cff"/>'
        '<text x="120" y="400" font-size="40" fill="var(--fg)">by hand</text></svg>\n```\n\nA [box](#r).\n'
    )
    data = tmp_path / "data"
    wid = _voiced(data, md, str(tmp_path))
    url = export_html(data, wid, tmp_path / "sketch.html").as_uri()
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"Chromium unavailable: {e}")
        page = b.new_page()
        other = []
        page.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.split("?")[0] == url else (other.append(route.request.url), route.abort()),
        )
        page.goto(url + "?capture=1")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        page.evaluate("(t) => window.__showntell.seek(t)", page.evaluate("window.__showntell.duration()") - 0.2)
        rough = page.evaluate("document.querySelector('.drawing').shadowRoot.querySelectorAll('.rough').length")
        font = page.evaluate("document.fonts.check('24px Virgil')")
        b.close()
    assert rough >= 1 and font and not other, (rough, font, other)


@pytest.mark.browser
def test_a_drawio_diagram_plays_offline_from_one_page(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from test_player_browser import ROOT

    from showntell.export import export_html

    md = (
        "# Diagram\n\nOpening words.\n\n## Architecture {#arch}\n\n```drawio file=tests/fixtures/drawio/architecture.drawio\n```\n\n"
        "The diagram as drawn.\n"
    )
    data = tmp_path / "data"
    wid = _voiced(data, md, str(ROOT))
    url = export_html(data, wid, tmp_path / "drawio.html").as_uri()
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"Chromium unavailable: {e}")
        page = b.new_page()
        other, errors = [], []
        page.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.split("?")[0] == url else (other.append(route.request.url), route.abort()),
        )
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "?capture=1")
        page.wait_for_function("window.__showntell?.duration?.() > 0")
        page.evaluate("(t) => window.__showntell.seek(t)", page.evaluate("window.__showntell.duration()") - 0.2)
        shapes = page.evaluate("document.querySelectorAll('.drawio svg g').length")
        b.close()
    assert shapes > 3 and not other and not errors, (shapes, other, errors)
