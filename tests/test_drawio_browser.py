"""A draw.io diagram in the player: drawn by the vendored viewer with the network blocked, the
pointer on its cells, parts revealed in order."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.slow]
pytest.importorskip("playwright.sync_api")

from test_player_browser import ROOT, _at, _rpc, browser, daemon  # noqa: E402,F401  (fixtures)

MD = """# draw.io

A diagram from draw.io.

## The system {#arch .build}

```drawio file=tests/fixtures/drawio/architecture.drawio
```

The [browser](#client) talks to [the API](#api) over [HTTPS](#e1).

Data lives in [Postgres](#db).
"""


def test_drawio_renders_offline_points_at_cells_and_reveals(daemon, browser):  # noqa: F811
    call = _rpc(daemon)
    r = call("start", title="draw.io", frame="landscape", voice="", speed=1.0, project_dir=str(ROOT))
    a = call("add", handle=r["handle"], markdown=MD)
    assert not [i for i in a["issues"] if i["severity"] == "error"], a["issues"]
    call("playable", handle=r["handle"], timeout=10)
    url = r["url"].replace("localhost", "127.0.0.1") + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    outside, errors = [], []
    page.route(
        "**/*",
        lambda route: (outside.append(route.request.url), route.abort()) if "127.0.0.1" not in route.request.url else route.continue_(),
    )
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function("window.__showntell !== undefined")
    t = _at(page, "arch", 0, 0.3)
    page.goto(f"{url}?at={t:.2f}")
    page.wait_for_selector(".drawio svg")
    page.wait_for_timeout(800)
    state = page.evaluate("""() => {
      const svg = document.querySelector('.drawio svg'), box = (e) => e.getBoundingClientRect().toJSON();
      const cell = svg.querySelector('[fill="#dae8fc"]'), p = document.querySelector('#pointer');
      return { cell: box(cell), pointer: p.hidden ? null : box(p), veiled: svg.querySelectorAll('.veiled').length,
               page: box(document.querySelector('.drawio')), body: box(document.querySelector('.slide-body')) }; }""")
    c, p = state["cell"], state["pointer"]
    assert p and p["left"] <= c["left"] + 2 and p["right"] >= c["right"] - 2 and p["top"] <= c["top"] + 2 and p["bottom"] >= c["bottom"] - 2
    assert state["veiled"] > 0  # later parts wait for their cues
    assert state["page"]["right"] <= state["body"]["right"] + 1 and state["page"]["bottom"] <= state["body"]["bottom"] + 1
    page.goto(url + "?capture=1")
    page.wait_for_function("window.__showntell?.duration?.() > 0")
    page.evaluate("(t) => window.__showntell.seek(t)", page.evaluate("window.__showntell.duration()") - 0.1)
    assert page.evaluate("document.querySelectorAll('.drawio .veiled').length") == 0
    assert not outside, outside
    assert not errors, errors
    page.close()
