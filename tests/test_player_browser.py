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


BASELINES = Path(__file__).parent / "baselines" / "linux"
MAX_CHANGED = 0.005  # fraction of pixels allowed to differ noticeably from the baseline


def compare_baseline(shot: Path) -> None:
    """Compare a screenshot with its Linux baseline. Fonts and Chromium builds differ between
    systems, so baselines exist only for Linux CI; elsewhere, and before baselines are added
    (scripts/update_baselines.sh), this does nothing."""
    base = BASELINES / shot.name
    if sys.platform != "linux" or not base.is_file():
        return
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(shot).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(base).convert("RGB"), dtype=np.int16)
    assert a.shape == b.shape, f"{shot.name}: size {a.shape} differs from the baseline {b.shape}"
    changed = float((np.abs(a - b).max(axis=2) > 24).mean())
    assert changed <= MAX_CHANGED, f"{shot.name}: {changed:.2%} of pixels differ from tests/baselines/linux"


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
        shot = ART / f"{frame}-{i:02d}-{slide['kind']}.png"
        # Let the slide finish easing in (the player then re-places the pointer) and every
        # transition settle, so the same frame comes out on every run.
        page.wait_for_function("document.getAnimations().every((a) => a.playState !== 'running')", timeout=5000)
        page.wait_for_timeout(150)
        page.screenshot(path=str(shot), animations="disabled")
        compare_baseline(shot)
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


def test_finish_draws_every_slide_and_reports_layout(daemon):
    call = _rpc(daemon)
    r = call("start", title="Unopened", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    long = "\n".join(f"value_{i} = compute_something_long(alpha, beta, gamma, delta, epsilon_{i})" for i in range(70))
    call("add", handle=r["handle"], markdown=f"# Unopened\n\nOpening words.\n\n## Wall {{#wall}}\n\n```python\n{long}\n```\n\nA wall of code.\n")
    call("playable", handle=r["handle"], timeout=10)
    f = call("finish", handle=r["handle"], questions=None)
    assert any(i["code"] == "text_too_small" and i["slide"] == "wall" for i in f["player_issues"])
    assert not any(i["code"] == "not_checked" and "player" in i["message"] for i in f["issues"])


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


# -- code that evolves, scrolling code, packets, numbers, regions and zoom, checklists ---------------

MORE = """# More kinds

The newer visuals.

## Part one {#part .chapter}
> Code

## Evolving {#evolve}

```python step
def total(xs):
    t = 0
    for x in xs:
        t += x
    return t
```
```python step
def total(xs):
    return sum(xs)
```

The loop [adds each value](#L4). Then it [becomes one call](#step-2) and [sum does the work](#L2 "sum").

## Long {#long}

```python file=src/showntell/build.py lines=1-150 scroll
```

First [the imports](#L9). Much later, [this line](#L140).

## Path {#path}

```mermaid
flowchart LR
  c[Client] --> g[Gateway] --> s[Service]
```

A request goes [all the way through](#c->g->s) and the [gateway](#g){.circle} checks it.

## Numbers {#numbers}

```stat
p95 latency: 400 ms -> 100 ms
```

The [p95 latency](#p95-latency) fell by three quarters, which took a lot of patient work over many weeks.

## Screen {#screen}

![The demo](docs/media/demo.gif){frame=browser url="example.com"}

The [top left](#@0,0,50,40){.zoom} is where it starts.

## Ship {#ship}

- [x] **Tests**: pass {#tests}
- [ ] **Announce**: later {#announce}

The [tests](#tests) pass, and the [announcement](#announce) waits.
"""


def _at(page, slide_id: str, cue: int, after: float) -> float:
    """Global time `after` seconds past cue number `cue` of a slide."""
    return page.evaluate(
        """([sid, ci, off]) => import('./' + document.body.dataset.static + '/js/timeline.js').then((m) =>
             fetch('data.json').then((r) => r.json()).then((w) => {
               const tl = m.build(w); const si = w.slides.findIndex((s) => s.id === sid); const at = [];
               for (const s of tl.segs.filter((s) => s.slide === si)) if (s.say >= 0)
                 for (const c of w.slides[si].says[s.say].cues || []) at.push(s.start + (c.t || 0));
               return at[ci] + off; }))""",
        [slide_id, cue, after],
    )


def test_newer_visuals_follow_the_narration(daemon, browser):
    call = _rpc(daemon)
    r = call("start", title="More kinds", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MORE)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    for _ in range(50):
        if not call("finish", handle=r["handle"], questions=None)["pending"]:
            break
        time.sleep(0.2)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    def at(slide_id, cue, after, js):
        page.goto(url)
        page.wait_for_function("window.__showntell !== undefined")
        t = _at(page, slide_id, cue, after)
        page.goto(f"{url}?t={t:.2f}")
        page.wait_for_timeout(700)
        return page.evaluate(js)

    page.goto(url + "?from=part&to=part")
    page.wait_for_selector(".k-chapter")
    assert page.text_content(".k-chapter .chapter-n") == "Part 1"
    assert at("evolve", 0, 0.3, "document.querySelector('.codebox .tag').textContent") == "Step 1 of 2"
    tag, line = at("evolve", 2, 0.3, "[document.querySelector('.codebox .tag').textContent, document.querySelector('.ln.hl .tx').textContent]")
    assert tag == "Step 2 of 2" and "sum" in line
    shift, focusing = at("long", 1, 0.3, "[getComputedStyle(document.querySelector('.codebox.scroll .code')).transform, document.querySelector('.codebox.scroll').classList.contains('focusing')]")
    assert shift not in ("none", "") and focusing
    x1 = at("path", 0, 0.2, "+document.querySelector('circle.packet').getAttribute('cx')")
    x2 = at("path", 0, 1.2, "+document.querySelector('circle.packet').getAttribute('cx')")
    assert x2 > x1 > 0  # the packet travels left to right
    assert at("path", 1, 0.3, "document.querySelector('#pointer').dataset.kind") == "circle"
    early = at("numbers", 0, 0.2, "document.querySelector('.stat .num').textContent")
    late = at("numbers", 0, 3.0, "document.querySelector('.stat .num').textContent")
    assert late == "100 ms" and early not in ("100 ms", "400 ms")
    inside, zoomed = at("screen", 0, 0.4, """(() => { const p = document.querySelector('#pointer').getBoundingClientRect();
        const i = document.querySelector('.device img').getBoundingClientRect();
        return [p.left >= i.left - 12 && p.top >= i.top - 12, !!document.querySelector('.slide-body').style.transform]; })()""")
    assert inside and zoomed
    ticked = at("ship", 1, 0.3, "[...document.querySelectorAll('.entry.ticked')].map((e) => e.dataset.id)")
    assert ticked == ["tests"]
    assert not errors, errors
    page.close()
