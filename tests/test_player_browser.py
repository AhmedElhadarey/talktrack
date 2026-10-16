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
