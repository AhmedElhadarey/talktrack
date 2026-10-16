"""Static export: a folder that plays from any web server, with WebVTT captions.

    out/index.html      the player, reading data.json instead of the live stream
    out/data.json       the walkthrough
    out/audio/          one file per narration paragraph (WAV, or MP3 with --mp3)
    out/assets/         images used by slides
    out/static/         player code and the bundled libraries
    out/captions.vtt    captions on the walkthrough's single timeline
    out/source.md       the Markdown the walkthrough was written in
    out/poster.png      the title slide, for link previews (only when Playwright is installed)

An output path ending in `.mp4` renders a video instead (video.py).
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


def _estimate(x) -> float:
    """A paragraph's length on the timeline, as `estimate` in timeline.js computes it."""
    if x.pause:
        return x.pause
    if x.status == "ready" and x.duration:
        return x.duration
    return max(1.0, len(x.text.split()) / 2.7)


def _walk(w: Walkthrough) -> tuple[list[tuple[float, object, object]], float]:
    out, t = [], 0.0
    for slide in w.slides:
        says = [x for x in slide.says if x.pause or x.text]
        if not says:
            t += EMPTY + SLIDE_GAP
            continue
        for k, x in enumerate(says):
            out.append((t, slide, x))
            t += _estimate(x) + (0 if x.pause else SLIDE_GAP if k == len(says) - 1 else GAP)
    return out, t


def timeline(w: Walkthrough) -> list[tuple[float, object, object]]:
    """(start, slide, say) for every paragraph, on the same clock as the player."""
    return _walk(w)[0]


def timeline_total(w: Walkthrough) -> float:
    """The walkthrough's length in seconds on the player's clock, trailing gap included."""
    return _walk(w)[1]


def _ts(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


# Caption grouping: break where the voice pauses or a sentence ends, keep groups short enough
# to read at a glance, show each a moment before its first word and let it linger briefly.
MAX_WORDS, MAX_SECONDS = 8, 3.0
PAUSE_BREAK, COMMA_PAUSE = 0.45, 0.2
LEAD, LINGER, MIN_SHOW = 0.08, 0.6, 0.7


def vtt(w: Walkthrough) -> str:
    """WebVTT captions on the player's timeline, one short phrase per cue."""
    cues: list[tuple[float, float, str]] = []
    for start, _, say in timeline(w):
        if say.pause or not say.text or not say.words:
            continue
        for group in phrases(say):
            a, b = group[0]["c0"], group[-1]["c1"]
            while b < len(say.text) and say.text[b] in ".,;:!?)\"'":  # keep trailing punctuation
                b += 1
            cues.append((start + group[0]["s"], start + group[-1]["e"], say.text[a:b].strip()))
    lines = ["WEBVTT", ""]
    for i, (s0, e0, text) in enumerate(cues):
        nxt = cues[i + 1][0] - LEAD if i + 1 < len(cues) else e0 + LINGER
        prev_end = cues[i - 1][1] if i else 0.0
        a = max(prev_end, s0 - LEAD) if i else max(0.0, s0 - LEAD)
        b = min(max(e0 + LINGER, a + MIN_SHOW), max(nxt, e0))
        lines += [str(i + 1), f"{_ts(a)} --> {_ts(b)}", text, ""]
    return "\n".join(lines)


def phrases(say) -> list[list[dict]]:
    """Group a paragraph's words into caption phrases using the voice's own timing."""
    out: list[list[dict]] = []
    group: list[dict] = []
    words = say.words
    for i, w_ in enumerate(words):
        group.append(w_)
        after = say.text[w_["c1"]: w_["c1"] + 1]
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["s"] - w_["e"]) if nxt else 0.0
        span = w_["e"] - group[0]["s"]
        if (nxt is None or after in (".", "?", "!") or gap >= PAUSE_BREAK
                or (after in (",", ";", ":") and (gap >= COMMA_PAUSE or len(group) >= 4))
                or len(group) >= MAX_WORDS or span >= MAX_SECONDS):
            out.append(group)
            group = []
    if group:
        out.append(group)
    return out


def export(data_dir: Path, wid: str, out: Path, mp3: bool = False, static_href: str = "static",
           copy_static: bool = True, poster: bool = False) -> Path:
    """Write the walkthrough to `out`. A gallery of several exports can share one copy of the
    player: pass `static_href="../static"` and `copy_static=False`, and copy `STATIC` once.
    `poster` also writes poster.png when Playwright is installed (needs `copy_static`)."""
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

    if copy_static:
        copy_player(out / "static")
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
    (out / "index.html").write_text(render_player(w.title, w.frame, "", static_href, live=False, theme=w.theme, tokens=w.tokens))
    (out / "captions.vtt").write_text(vtt(w))
    src_md = data_dir / "walkthroughs" / wid / "source.md"
    if src_md.is_file():
        (out / "source.md").write_text(src_md.read_text())
    if poster and copy_static and write_poster(out):
        page = (out / "index.html").read_text()
        meta = ('<meta property="og:image" content="poster.png">\n'
                '<meta name="twitter:card" content="summary_large_image">\n'
                '<meta name="twitter:image" content="poster.png">\n')
        (out / "index.html").write_text(page.replace("</head>", meta + "</head>", 1))
    return out


def write_poster(folder: Path) -> bool:
    """poster.png: the title slide's settled frame. False, silently, without Playwright or its
    browser; a warning for any other failure."""
    from .capture import CaptureUnavailable, capture_frames

    try:
        png = capture_frames(folder, [0.0])[0]
    except CaptureUnavailable:
        return False
    except Exception as e:  # a poster is a nicety; never fail the export over it
        print(f"warning: no poster.png: {e}", file=sys.stderr)
        return False
    (folder / "poster.png").write_bytes(png)
    return True


def copy_player(dest: Path) -> None:
    shutil.copytree(STATIC, dest, ignore=shutil.ignore_patterns("index.html", "player.html", "*.map"), dirs_exist_ok=True)


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
    dest = Path(args.out).expanduser()
    if dest.suffix.lower() == ".mp4":
        return _export_mp4(cfg, wid, dest, args)
    out = export(cfg.data_dir, wid, dest, mp3=args.mp3, poster=True)
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    print(f"Exported to {out} ({size / 1e6:.1f} MB). Serve the folder with any web server, for example:")
    print(f"  python3 -m http.server --directory {out} 8000")


def _export_mp4(cfg: Config, wid: str, dest: Path, args) -> None:
    from .video import render_mp4

    if args.mp3:
        raise SystemExit("--mp3 applies to folder exports; an MP4 always carries AAC audio")
    r = render_mp4(cfg.data_dir, wid, dest, fps=args.fps, workers=args.workers, captions=args.burn_captions)
    for e in r["errors"]:
        print(f"- player: {e}", file=sys.stderr)
    print(f"Exported to {dest} ({dest.stat().st_size / 1e6:.1f} MB, {r['duration']:.1f} s, {r['frames']} frames at "
          f"{r['fps']} fps) in {r['seconds']:.1f} s: {r['speed']:.1f} s of video per second")


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
