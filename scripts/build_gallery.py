"""Build gallery/: exported walkthroughs sharing one copy of the player, plus an index page.

    uv run python scripts/build_gallery.py gallery.json
    uv run python scripts/build_gallery.py --from-results eval/results/<run>.json --data <eval data folder>

gallery.json lists entries: [{"slug": "...", "id": "<walkthrough id>", "blurb": "..."}], read
from the data folder in SHOWNTELL_DATA (default ~/.local/share/showntell). With
--from-results, the entries come from an eval run made with --keep, using each prompt as
the blurb.
"""

from __future__ import annotations

import html
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from showntell.config import Config  # noqa: E402
from showntell.export import copy_player, export, timeline  # noqa: E402
from showntell.model import Walkthrough  # noqa: E402

OUT = ROOT / "gallery"


def _entries() -> tuple[list[dict], Config]:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("entries", nargs="?")
    ap.add_argument("--from-results")
    ap.add_argument("--data")
    args = ap.parse_args()
    cfg = Config()
    if args.data:
        cfg.data_dir = Path(args.data)
    if args.from_results:
        prompts = {}
        for f in (ROOT / "eval").glob("*.json"):
            for p in json.loads(f.read_text()):
                prompts[p["id"]] = p["prompt"]
        entries = []
        for r in json.loads(Path(args.from_results).read_text()):
            if not r.get("url"):
                continue
            blurb = prompts.get(r["id"], "").removeprefix("Use showntell to ").split(". ")[0]
            entries.append({"slug": r["id"], "id": r["url"].rstrip("/").rsplit("/", 1)[-1],
                            "blurb": f"Asked to {blurb}." if blurb else ""})
        return entries, cfg
    return json.loads(Path(args.entries).read_text()), cfg


def main() -> None:
    entries, cfg = _entries()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    copy_player(OUT / "static")
    cards = []
    for e in entries:
        out = export(cfg.data_dir, e["id"], OUT / e["slug"], mp3=True, static_href="../static", copy_static=False)
        w = Walkthrough.from_state(json.loads((cfg.data_dir / "walkthroughs" / e["id"] / "state.json").read_text()))
        tl = timeline(w)
        end = tl[-1][0] + (tl[-1][2].duration or tl[-1][2].pause) if tl else 0
        cards.append(
            f'<li><a href="{e["slug"]}/"><span class="frame {w.frame}"></span><span class="t">{html.escape(w.title)}</span>'
            f'<span class="b">{html.escape(e.get("blurb", ""))}</span>'
            f'<span class="m">{len(w.slides)} slides · {int(end // 60)}:{int(end % 60):02d}</span></a></li>'
        )
        print(f"exported {e['slug']}: {out}")
    (OUT / "index.html").write_text(PAGE.replace("{{CARDS}}", "\n".join(cards)))
    (OUT / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"gallery: {OUT} ({size / 1e6:.1f} MB)")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>showntell gallery</title><link rel="icon" href="static/icon.svg" type="image/svg+xml">
<style>
:root { color-scheme: dark; --bg: #0a0c10; --panel: #151a22; --line: #262e3b; --fg: #ebeef3; --muted: #98a2b3; --accent: #ffc857; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 64px 16px 96px; }
header { display: flex; align-items: center; gap: 14px; }
header img { width: 40px; height: 40px; }
h1 { margin: 0; font-size: 34px; letter-spacing: -0.02em; }
.lede { color: var(--muted); font-size: 18px; max-width: 680px; margin: 14px 0 36px; }
.lede a { color: var(--accent); }
ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 18px; }
li a { display: flex; flex-direction: column; gap: 8px; height: 100%; padding: 20px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 16px; color: inherit; text-decoration: none; transition: border-color 0.2s, transform 0.2s; }
li a:hover, li a:focus-visible { border-color: var(--accent); transform: translateY(-2px); }
.frame { display: block; width: 44px; height: 26px; border-radius: 5px; background: linear-gradient(135deg, #1f2733, #2b3442); border: 1px solid var(--line); }
.frame.square { width: 30px; height: 30px; } .frame.portrait { width: 20px; height: 34px; }
.t { font-weight: 700; font-size: 19px; line-height: 1.3; }
.b { color: var(--muted); font-size: 15px; flex: 1; }
.m { color: var(--muted); font-size: 13px; }
footer { margin-top: 48px; color: var(--muted); font-size: 14px; }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
</style></head>
<body><main>
<header><img src="static/icon.svg" alt=""><h1>showntell</h1></header>
<p class="lede">Narrated walkthroughs written by coding agents and voiced on a laptop with Kokoro. Each one below was made by asking an agent a question; nothing was edited by hand. <a href="https://github.com/rahimnathwani/showntell">Get showntell</a>.</p>
<ul>
{{CARDS}}
</ul>
<footer>Exported with <code>showntell export</code>. Audio is MP3; captions are in each folder as <code>captions.vtt</code>.</footer>
</main></body></html>
"""

if __name__ == "__main__":
    main()
