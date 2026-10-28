# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright", "httpx"]
# ///
"""Render code and diff slides of growing size in each frame and report the zoom the player
applies. A zoom of about 0.82 or more reads comfortably; build.LIMITS and docs/format.md use this.

    uv run talktrack serve --speech fake --port 4803 --data /tmp/talktrack-measure &
    uv run scripts/measure_limits.py /tmp/talktrack-measure/daemon.json
"""

import asyncio
import json
import sys

import httpx
from playwright.async_api import async_playwright

lock = json.load(open(sys.argv[1]))
base = f"http://127.0.0.1:{lock['port']}"
H = {"Authorization": f"Bearer {lock['token']}"}


def rpc(m, **kw):
    r = httpx.post(f"{base}/api/rpc/{m}", json=kw, headers=H, timeout=30)
    r.raise_for_status()
    return r.json()["result"]


SIZES = {
    "code": [
        (10, 40),
        (14, 40),
        (18, 40),
        (22, 40),
        (26, 40),
        (30, 40),
        (34, 40),
        (12, 50),
        (12, 60),
        (12, 70),
        (12, 80),
        (12, 90),
        (12, 100),
    ],
    "diff": [(10, 30), (14, 30), (16, 30), (18, 30), (20, 30), (22, 30), (8, 36), (8, 42), (8, 48), (8, 54), (8, 60)],
}


def code(lines, width):
    row = ("x = compute(alpha, beta) + " + "y" * 200)[:width]
    return "\n".join(row for _ in range(lines))


async def zoom_for(page, url):
    await page.goto(url + "?at=4")
    await page.wait_for_timeout(700)
    return await page.evaluate(
        "(() => { const c = document.querySelector('.slide-body > *'); return c ? parseFloat(c.dataset.scale || 1) : null })()"
    )


async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1600, "height": 1000})
        for frame in ("landscape", "square", "portrait"):
            for kind in ("code", "diff"):
                for lines, width in SIZES[kind]:
                    r = rpc("start", title="m", frame=frame, voice="", speed=1.0, project_dir=None)
                    if kind == "code":
                        md = f"# m\n\nHello there.\n\n## c\n\n```python\n{code(lines, width)}\n```\n\nThe code is here.\n"
                    else:
                        md = f"# m\n\nHello there.\n\n## c\n\n```python old\n{code(lines, width)}\n```\n```python new\n{code(lines, width)}\n```\n\nThe code is here.\n"
                    rpc("add", handle=r["handle"], markdown=md)
                    z = await zoom_for(page, r["url"].replace("localhost", "127.0.0.1") + "/")
                    out.setdefault(f"{frame}/{kind}", []).append((lines, width, z))
        await b.close()
    for k, rows in out.items():
        print(k, " ".join(f"{n}x{w}:{z:.2f}" for n, w, z in rows))


asyncio.run(main())
