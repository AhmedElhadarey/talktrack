# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright", "httpx", "numpy", "soundfile"]
# ///
"""Record a walkthrough as an MP4 with its narration, and optionally a short GIF.

Plays the walkthrough at 1x in headless Chromium while recording the page, rebuilds the
narration track from data.json on the same timeline as the player, and muxes both with ffmpeg.

    uv run scripts/record_video.py http://localhost:4747/w/<id>/ demo.mp4 --gif demo.gif --gif-seconds 20
"""

from __future__ import annotations

import argparse
import asyncio
import io
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from playwright.async_api import async_playwright

GAP, SLIDE_GAP, EMPTY, SR = 0.3, 0.6, 2.5, 24000  # same as static/js/timeline.js


def narration_track(url: str) -> np.ndarray:
    data = httpx.get(url + "data.json", timeout=30).json()
    clips, t = [], 0.0
    for slide in data["slides"]:
        says = [x for x in slide["says"] if x.get("pause") or x.get("text")]
        if not says:
            t += EMPTY + SLIDE_GAP
            continue
        for k, x in enumerate(says):
            if x.get("pause"):
                t += x["pause"]
                continue
            dur = x.get("duration") or len(x.get("text", "").split()) / 2.7
            if x.get("audio"):
                audio, sr = sf.read(io.BytesIO(httpx.get(url + x["audio"], timeout=30).content), dtype="float32")
                assert sr == SR
                clips.append((t, audio))
            t += dur + (SLIDE_GAP if k == len(says) - 1 else GAP)
    track = np.zeros(int((t + 1) * SR), dtype=np.float32)
    for start, audio in clips:
        i = int(start * SR)
        track[i : i + len(audio)] += audio[: len(track) - i]
    return track


async def record(url: str, size: tuple[int, int], workdir: Path) -> tuple[Path, float]:
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        ctx = await b.new_context(viewport={"width": size[0], "height": size[1]}, record_video_dir=str(workdir),
                                  record_video_size={"width": size[0], "height": size[1]})
        t_start = time.monotonic()
        page = await ctx.new_page()
        await page.goto(url)
        await page.wait_for_function("window.__showntell !== undefined && !document.querySelector('#big-play').disabled")
        await page.wait_for_timeout(800)
        offset = time.monotonic() - t_start
        await page.click("#big-play")
        while True:
            await page.wait_for_timeout(500)
            if await page.evaluate("window.__showntell.ended"):
                break
        await page.wait_for_timeout(2500)  # hold the end card
        video = await page.video.path()
        await ctx.close()
        await b.close()
        return Path(video), offset


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--gif")
    ap.add_argument("--gif-seconds", type=float, default=20)
    ap.add_argument("--gif-start", type=float, default=0)
    ap.add_argument("--gif-width", type=int, default=880)
    args = ap.parse_args()
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is needed")
    url = args.url if args.url.endswith("/") else args.url + "/"
    size = tuple(int(x) for x in args.size.split("x"))
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        video, offset = asyncio.run(record(url, size, work))
        wav = work / "narration.wav"
        sf.write(wav, narration_track(url), SR)
        delay = int(offset * 1000) + 120  # the click lands a frame or so after the measurement
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav),
                        "-filter_complex", f"[1:a]adelay={delay}|{delay}[a]", "-map", "0:v", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "22", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", args.out], check=True)
        print(f"video: {args.out}")
        if args.gif:
            start = offset + args.gif_start
            vf = f"fps=10,scale={args.gif_width}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-t", str(args.gif_seconds),
                            "-i", str(video), "-vf", vf, args.gif], check=True)
            print(f"gif: {args.gif}")


if __name__ == "__main__":
    main()
