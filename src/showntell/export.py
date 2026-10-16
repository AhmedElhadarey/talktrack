"""Static export: a folder that plays from any web server, with WebVTT captions.

    out/index.html      the player, reading data.json instead of the live stream
    out/data.json       the walkthrough
    out/audio/          one file per narration paragraph (WAV, or MP3 with --mp3)
    out/assets/         images used by slides
    out/static/         player code and the bundled libraries
    out/captions.vtt    captions on the walkthrough's single timeline
    out/source.md       the Markdown the walkthrough was written in
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

from .config import Config
from .model import Walkthrough

GAP, SLIDE_GAP, EMPTY = 0.3, 0.6, 2.5  # keep in step with static/js/timeline.js
STATIC = Path(__file__).parent / "static"


def timeline(w: Walkthrough) -> list[tuple[float, object, object]]:
    """(start, slide, say) for every paragraph, on the same clock as the player."""
    out, t = [], 0.0
    for slide in w.slides:
        says = [x for x in slide.says if x.pause or x.text]
        if not says:
            t += EMPTY + SLIDE_GAP
            continue
        for k, x in enumerate(says):
            dur = x.pause or x.duration or len(x.text.split()) / 2.7
            out.append((t, slide, x))
            t += dur + (0 if x.pause else SLIDE_GAP if k == len(says) - 1 else GAP)
    return out


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def vtt(w: Walkthrough, max_words: int = 12) -> str:
    """Captions grouped into short phrases, breaking at sentence ends."""
    lines = ["WEBVTT", ""]
    n = 0
    for start, _, say in timeline(w):
        if say.pause or not say.text or not say.words:
            continue
        group: list[dict] = []

        def flush() -> None:
            nonlocal n, group
            if not group:
                return
            a, b = group[0]["c0"], group[-1]["c1"]
            # extend to trailing punctuation
            while b < len(say.text) and say.text[b] in ".,;:!?)\"'":
                b += 1
            n += 1
            lines.extend([str(n), f"{_ts(start + group[0]["s"])} --> {_ts(start + group[-1]["e"] + 0.15)}", say.text[a:b].strip(), ""])
            group = []

        for w_ in say.words:
            group.append(w_)
            tail = say.text[w_["c1"] : w_["c1"] + 1]
            if len(group) >= max_words or tail in (".", "?", "!"):
                flush()
        flush()
    return "\n".join(lines)


def export(data_dir: Path, wid: str, out: Path, mp3: bool = False) -> Path:
    state = data_dir / "walkthroughs" / wid / "state.json"
    if not state.is_file():
        raise SystemExit(f"no walkthrough {wid} in {data_dir}")
    w = Walkthrough.from_state(json.loads(state.read_text()))
    pending = [x for s in w.slides for x in s.says if x.text and x.status != "ready"]
    if pending:
        print(f"warning: {len(pending)} paragraph(s) have no audio yet and will be shown without sound", file=sys.stderr)
    if out.exists() and any(out.iterdir()):
        if not (out / "data.json").is_file():
            raise SystemExit(f"{out} exists and is not a showntell export; choose an empty folder")
        shutil.rmtree(out)
    (out / "audio").mkdir(parents=True)
    from .server import render_player

    shutil.copytree(STATIC, out / "static", ignore=shutil.ignore_patterns("index.html", "player.html", "*.map"))
    assets = data_dir / "walkthroughs" / wid / "assets"
    if assets.is_dir():
        shutil.copytree(assets, out / "assets")
    audio_dir = data_dir / "audio"
    data = w.public()
    for slide in data["slides"]:
        for say in slide["says"]:
            if "audio" not in say:
                continue
            key = Path(say["audio"]).stem
            src = audio_dir / f"{key}.wav"
            if not src.is_file():
                say.pop("audio")
                continue
            if mp3:
                import soundfile as sf

                audio, sr = sf.read(src, dtype="float32")
                sf.write(out / "audio" / f"{key}.mp3", audio, sr, format="MP3")
                say["audio"] = f"audio/{key}.mp3"
            else:
                shutil.copyfile(src, out / "audio" / f"{key}.wav")
    data["status"] = "finished"
    (out / "data.json").write_text(json.dumps(data))
    (out / "index.html").write_text(render_player(w.title, w.frame, "", "static", live=False))
    (out / "captions.vtt").write_text(vtt(w))
    src_md = data_dir / "walkthroughs" / wid / "source.md"
    if src_md.is_file():
        (out / "source.md").write_text(src_md.read_text())
    return out


def _resolve_id(source: str) -> str | None:
    m = re.search(r"/w/([A-Za-z0-9_-]{16,64})", source)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", source):
        return source
    return None


def export_cli(cfg: Config, args) -> None:
    wid = _resolve_id(args.source)
    if wid is None:
        path = Path(args.source).expanduser()
        if not path.is_file():
            raise SystemExit(f"{args.source} is neither a walkthrough id, a link nor a Markdown file")
        wid = _play_and_wait(cfg, path)
    out = export(cfg.data_dir, wid, Path(args.out).expanduser(), mp3=args.mp3)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Exported to {out} ({size / 1e6:.1f} MB). Serve the folder with any web server, for example:")
    print(f"  python3 -m http.server --directory {out} 8000")


def _play_and_wait(cfg: Config, path: Path) -> str:
    """Voice a Markdown file through the daemon and wait until every paragraph has audio."""
    import asyncio

    from . import daemon
    from .cli import _project_for, file_meta
    from .service import RemoteService

    lock = daemon.ensure(cfg, log=lambda m: print(m, file=sys.stderr))
    text = path.read_text()
    meta = file_meta(path, text)

    async def go() -> str:
        svc = RemoteService(f"http://127.0.0.1:{lock['port']}", lock["token"])
        r = await svc.start(meta["title"], meta["frame"], meta["voice"], meta["speed"], str(_project_for(path)))
        await svc.add(r["handle"], text)
        await svc.playable(r["handle"], 60)
        for _ in range(600):
            f = await svc.finish(r["handle"], None)
            if not f["pending"]:
                break
            await asyncio.sleep(0.5)
        return r["handle"].split(".")[0]

    return asyncio.run(go())
