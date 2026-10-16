"""Every slide kind in every frame, rendered in headless Chromium against a fake-speech daemon.

Checks that each slide renders without errors, its visual fits inside the stage, and the
pointer lands inside the stage when a cue is active. Screenshots go to tests/artifacts/ for
review. Needs Playwright's Chromium: `uv run playwright install chromium`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pw = pytest.importorskip("playwright.sync_api")

ART = Path(__file__).parent / "artifacts"
ROOT = Path(__file__).resolve().parents[1]

DOC = """# Every slide kind
> A tour of what the player draws

This opening sentence is narrated while the title shows.

## Code from disk {#code .build}

```python file=src/showntell/format.py lines=1-14
```

The file opens with [a docstring](#L1) and then [imports](#L8 "import re").

## A change {#diff}

```python old
total = 0
for x in xs:
    total += x
```
```python new
total = sum(xs)
```

The loop [goes away](#-L2) and [one call](#+L1 "sum") replaces it.

## Flow {#flow .build}

```mermaid
flowchart LR
  a[Request] --> b{Cached?}
  b -->|yes| c[Return]
  b -->|no| d[Load]
```

A [request](#a) checks [the cache](#b) and either [returns](#c) or [loads](#d).

## Steps {#steps .build}

1. **Parse**: text to blocks {#parse}
2. **Build**: blocks to slides {#build}
3. **Voice**: slides to audio {#voice}

First we [parse](#parse), then [build](#build), then [voice](#voice).

## Options {#cards .cards}

- **Memory**: fast, lost on restart {#mem}
- **Disk**: slower, survives restarts {#disk}
- **Network**: shared between machines {#net}

[Memory](#mem) is fastest and [the network](#net) is shared.

## Checks {#table}

| Check | When |
|---|---|
| Cue target | every call |
| Pacing | slide end |

[Cue targets](#row-1) are checked on every call.

## Softmax {#math}

```math
\\mathrm{softmax}(z)_i = \\frac{e^{z_i}}{\\sum_j e^{z_j}}
```

Each output is [an exponential](#) divided by the sum of all of them.

## Plain words {#text}

> Shown text, not spoken.

A slide with only words on it.
"""

KINDS = ["title", "code", "diff", "diagram", "list", "list", "table", "math", "text"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    data = tmp_path_factory.mktemp("browser-data")
    port = _free_port()
    env = {**os.environ, "SHOWNTELL_DATA": str(data), "SHOWNTELL_SPEECH": "fake"}
    proc = subprocess.Popen([sys.executable, "-m", "showntell", "serve", "--port", str(port), "--speech", "fake"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    lock = None
    while time.time() < deadline:
        if (data / "daemon.json").is_file():
            lock = json.loads((data / "daemon.json").read_text())
            try:
                if httpx.get(f"http://127.0.0.1:{lock['port']}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
        time.sleep(0.2)
    assert lock, "daemon did not start"
    yield lock
    proc.terminate()
    proc.wait(10)


def _make(lock: dict, frame: str) -> str:
    h = {"Authorization": f"Bearer {lock['token']}"}
    base = f"http://127.0.0.1:{lock['port']}/api/rpc"

    def call(m, **kw):
        r = httpx.post(f"{base}/{m}", json=kw, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()["result"]

    r = call("start", title="Every slide kind", frame=frame, voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=DOC)
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=["What next?"])["pending"]:
            break
        time.sleep(0.2)
    return r["url"].replace("localhost", "127.0.0.1") + "/"


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browsers not installed
            pytest.skip(f"Chromium unavailable: {e}")
        yield b
        b.close()


@pytest.mark.parametrize("frame", ["landscape", "square", "portrait"])
def test_every_kind_renders(daemon, browser, frame):
    url = _make(daemon, frame)
    data = httpx.get(url + "data.json").json()
    assert [s["kind"] for s in data["slides"]] == KINDS
    page = browser.new_page(viewport={"width": 1440, "height": 900} if frame != "portrait" else {"width": 900, "height": 1400})
    ART.mkdir(exist_ok=True)
    for i, slide in enumerate(data["slides"]):
        # a moment after the first cue of the slide, or a second in
        page.goto(url)
        page.wait_for_function("window.__showntell !== undefined")
        t = page.evaluate(
            """(i) => new Promise((res) => {
                 import('./' + document.body.dataset.static + '/js/timeline.js').then((m) => {
                   fetch('data.json').then((r) => r.json()).then((w) => {
                     const tl = m.build(w); const seg = tl.segs.find((s) => s.slide === i);
                     const cues = w.slides[i].says.flatMap((x) => x.cues || []);
                     const first = cues.length ? Math.max(0, cues[cues.length - 1].t || 0) : 0.5;
                     res(seg.start + Math.min(first + 0.2, seg.dur));
                   });
                 });
               })""",
            i,
        )
        page.goto(f"{url}?t={t:.2f}")
        page.wait_for_timeout(900 if slide["kind"] in ("diagram", "math") else 400)
        page.evaluate("document.querySelector('#overlay').hidden = true")
        state = page.evaluate(
            """() => {
              const stage = document.querySelector('#stage').getBoundingClientRect();
              const slide = document.querySelector('.slide');
              const ptr = document.querySelector('#pointer');
              const p = ptr.hidden ? null : ptr.getBoundingClientRect();
              const body = document.querySelector('.slide-body');
              const vis = body && body.firstElementChild ? body.firstElementChild.getBoundingClientRect() : null;
              return {cls: slide && slide.className, errors: window.__showntell.renderErrors,
                      stage: stage.toJSON(), pointer: p && p.toJSON(), visual: vis && vis.toJSON()};
            }"""
        )
        page.screenshot(path=str(ART / f"{frame}-{i:02d}-{slide['kind']}.png"))
        assert f"k-{slide['kind']}" in state["cls"]
        assert not state["errors"], state["errors"]
        st = state["stage"]
        if state["visual"]:
            v = state["visual"]
            assert v["left"] >= st["left"] - 2 and v["right"] <= st["right"] + 2, f"{frame} slide {i} overflows sideways"
            assert v["bottom"] <= st["bottom"] + 2, f"{frame} slide {i} overflows downwards"
        if state["pointer"]:
            p = state["pointer"]
            assert p["left"] >= st["left"] - 20 and p["right"] <= st["right"] + 20
            assert p["top"] >= st["top"] - 20 and p["bottom"] <= st["bottom"] + 20
    page.close()


# -- ranges, presenting, embedding, layout findings -------------------------------------------------


def _rpc(lock: dict):
    h = {"Authorization": f"Bearer {lock['token']}"}
    base = f"http://127.0.0.1:{lock['port']}/api/rpc"

    def call(m, **kw):
        r = httpx.post(f"{base}/{m}", json=kw, headers=h, timeout=30)
        r.raise_for_status()
        return r.json()["result"]

    return call


def test_a_range_plays_its_slides_and_stops(daemon, browser):
    url = _make(daemon, "landscape")
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url + "?from=flow&to=flow")
    page.wait_for_function("window.__showntell !== undefined && !document.querySelector('#big-play').disabled")
    page.click("#big-play")
    page.wait_for_selector("#range-note:not([hidden])", timeout=20000)
    shown = page.evaluate("document.querySelector('.slide').className")
    assert "k-diagram" in shown and not page.evaluate("window.__showntell.ended")
    page.close()


def test_presenting_steps_by_hand_without_voice(daemon, browser):
    url = _make(daemon, "landscape")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(url + "?present=1")
    page.wait_for_selector("#notes:not([hidden])")
    first = page.inner_text("#notes-step")
    for _ in range(4):
        page.keyboard.press("Space")
    page.wait_for_timeout(300)
    assert page.inner_text("#notes-step") != first and "step 5 of" in page.inner_text("#notes-step")
    assert page.evaluate("window.__showntell.starts.length") == 0  # nothing was voiced
    page.close()


def test_embedded_player_sends_events_and_takes_commands(daemon, browser):
    url = _make(daemon, "landscape")
    embed = f"http://127.0.0.1:{daemon['port']}/static/embed.js"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    host = f"""<!doctype html><script src="{embed}"></script>
        <showntell-player id="p" src="{url}"></showntell-player>
        <script>window.got = []; for (const t of ["ready", "slidechange", "durationchange"])
          document.getElementById("p").addEventListener(t, (e) => got.push([t, e.detail]));</script>"""
    # A page on another origin (another local port), as a docs site embedding a walkthrough.
    import http.server
    import threading

    class Host(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(host.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    page.goto(f"http://127.0.0.1:{srv.server_port}/")
    page.wait_for_function("window.got.some(([t]) => t === 'ready')", timeout=15000)
    page.evaluate("document.getElementById('p').seek(document.getElementById('p').duration - 0.5)")
    page.wait_for_function("window.got.some(([t, d]) => t === 'slidechange' && d.id === 'text')", timeout=10000)
    ready = page.evaluate("window.got.find(([t]) => t === 'ready')[1]")
    assert ready["slides"] == len(KINDS) and ready["duration"] > 0
    page.close()
    srv.shutdown()


def test_the_player_reports_text_shrunk_too_far(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="Too big", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    long = "\n".join(f"value_{i} = compute_something_long(alpha, beta, gamma, delta, epsilon_{i})" for i in range(70))
    call("add", handle=r["handle"], markdown=f"# Too big\n\nOpening words.\n\n## Wall {{#wall}}\n\n```python\n{long}\n```\n\nA wall of code.\n")
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(url + "?from=wall")
    page.wait_for_function("(window.__showntell.findings || []).some((f) => f.code === 'text_too_small')", timeout=10000)
    page.wait_for_timeout(300)
    f = call("finish", handle=r["handle"], questions=None)
    assert any(i["code"] == "text_too_small" and i["slide"] == "wall" and i["source"] == "player" for i in f["player_issues"])
    page.close()


def test_preview_returns_pictures_of_slides(daemon, browser):
    import base64

    call = _rpc(daemon)
    r = call("start", title="Preview", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    call("add", handle=r["handle"], markdown=DOC)
    call("playable", handle=r["handle"], timeout=10)
    p = call("preview", handle=r["handle"], slides=["flow", "math", "nope"], short=360)
    assert [s["id"] for s in p["slides"]] == ["flow", "math"] and p["unknown"] == ["nope"]
    png = base64.b64decode(p["slides"][0]["png"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000
    assert p["slides"][0]["png"] != p["slides"][1]["png"]
