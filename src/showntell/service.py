"""What the MCP tools do, independent of transport.

`LocalService` works on the store inside the daemon. `RemoteService` forwards the same calls
to a running daemon over its token-protected RPC route; the stdio shim uses it, so every
MCP client shares one daemon and one loaded voice model.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .issues import as_dict
from .store import Store


def _dicts(items) -> list[dict]:
    return [as_dict(i) for i in items]


class LocalService:
    def __init__(self, store: Store, layout_check: bool = False) -> None:
        self.store = store
        self.layout_check = layout_check  # draw every slide headless at finish (the daemon turns this on)

    def speech_status(self) -> dict:
        n = self.store.narrator
        return {"ready": n.ready, "state": n.status.state, "describe": n.status.describe()}

    async def start(self, title: str, frame: str, voice: str, speed: float, project_dir: str | None, theme: str = "") -> dict:
        root = None
        if project_dir:
            p = Path(project_dir).expanduser()
            if not p.is_dir():
                raise ValueError(f"project_dir {project_dir} is not a folder")
            root = str(p.resolve())
        s = self.store.create(title, frame, voice, speed, root, theme)
        return {"handle": s.handle, "url": s.url, "frame": s.w.frame, "root": root, "speech": self.speech_status()}

    async def add(self, handle: str, markdown: str) -> dict:
        s = self.store.by_handle(handle)
        r = s.add(markdown)
        r["issues"] = _dicts(r["issues"] + s.take_late_issues())
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        return {
            **r,
            "url": s.url,
            "frame": s.w.frame,
            "link_shared": s.w.link_shared,
            "total_slides": len(s.w.slides),
            "total_says": len(says),
            "voiced": sum(1 for x in says if x.status == "ready"),
            "narration_s": round(sum(x.duration or len(x.text.split()) / 2.7 for x in says), 1),
            "player_issues": _dicts(s.take_player_issues()),
            "slide_ids": [sl.id for sl in s.w.slides],
            "speech": self.speech_status(),
        }

    async def playable(self, handle: str, timeout: float, give_up: bool = False) -> dict:
        """Wait up to `timeout` for the opening narration. Marks the link shared once it is,
        or at once with `give_up`, so later calls stop waiting."""
        s = self.store.by_handle(handle)
        status = await s.wait_playable(timeout) if timeout > 0 else (s.first_say().status if s.first_say() else "empty")
        if give_up and status == "pending":
            s.w.link_shared = True
        out: dict[str, Any] = {"status": status, "url": s.url, "speech": self.speech_status(), "frame": s.w.frame}
        if status == "error":
            first = s.first_say()
            out["error"] = first.error if first else ""
        if status in ("ready", "error"):
            out["guide"] = not s.w.guide_sent
            first_time = not s.w.link_shared or not s.w.guide_sent
            s.w.link_shared = True
            s.w.guide_sent = True
            if first_time:
                s._changed(set(), meta=True)
                if status == "ready":
                    self.store.on_playable(s)
        return out

    async def revise(self, handle: str, slide: str, markdown: str) -> dict:
        s = self.store.by_handle(handle)
        r = s.revise(slide, markdown)
        r["issues"] = _dicts(r["issues"] + s.take_late_issues())
        return {**r, "url": s.url, "player_issues": _dicts(s.take_player_issues())}

    async def finish(self, handle: str, questions: list[str] | None, check: bool = True) -> dict:
        """Close the walkthrough. `check=False` skips drawing the slides headless, for callers
        that only wait for the audio (exporting a Markdown file)."""
        from .checks import look
        from .export import slide_starts, timeline_total

        s = self.store.by_handle(handle)
        layout = self.layout_check and check
        unchecked = await self._check_layout(s) if layout else ""
        issues = s.finish(questions, unchecked=unchecked if layout else None) + s.take_late_issues()
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        starts = slide_starts(s.w)
        return {
            "outline": [{"id": sl.id, "title": sl.title, "start_s": round(starts[sl.id], 1), "look": look(sl)} for sl in s.w.slides],
            "url": s.url,
            "slides": len(s.w.slides),
            "duration_s": round(timeline_total(s.w), 1),
            "pending": sum(1 for x in says if x.status == "pending"),
            "issues": _dicts(issues),
            "player_issues": _dicts(s.take_player_issues()),
            "questions": s.w.questions,
        }

    async def list(self) -> list[dict]:
        return self.store.list(20)

    async def _check_layout(self, s) -> str:
        """Draw every slide fully revealed in headless Chromium, so the player's render and layout
        checks report before the agent stops. Returns why it could not run, or ''."""
        import asyncio

        from .capture import Capturer, CaptureUnavailable

        ends = _end_times(s.w)
        if not ends:
            return ""

        async def run() -> None:
            async with Capturer(s.url, short=360) as c:
                for t in list(ends.values())[:LAYOUT_CHECK_MAX]:
                    await c.seek(t, measure=True)
                for f in await c.page.evaluate("window.__showntell.findings || []"):
                    s.report(f)

        try:
            await asyncio.wait_for(run(), LAYOUT_CHECK_S)
        except CaptureUnavailable as e:
            return str(e)
        except TimeoutError:
            return f"drawing the slides took over {LAYOUT_CHECK_S} s"
        except Exception as e:  # a broken page or browser must not fail finish
            return f"the headless player failed: {str(e).splitlines()[0][:200]}"
        return ""

    async def preview(self, handle: str, slides: list[str] | None = None, short: int = 360) -> dict:
        """Pictures of slides as the viewer sees them once fully revealed, for the agent to check."""
        import base64

        from .capture import CaptureUnavailable, capture_frames_async

        s = self.store.by_handle(handle)
        ends = _end_times(s.w)
        order = list(ends)
        want = [x for x in (slides or order) if x in ends]
        unknown = [x for x in (slides or []) if x not in ends]
        if len(want) > PREVIEW_MAX:
            want = want[-PREVIEW_MAX:] if not slides else want[:PREVIEW_MAX]
        times = [ends[sid] for sid in want]
        try:
            pngs = await capture_frames_async(s.url, times, short=short, measure=True) if times else []
        except CaptureUnavailable as e:
            raise ValueError(str(e)) from e
        return {
            "url": s.url,
            "slides": [
                {
                    "id": sid,
                    "title": next(x.title for x in s.w.slides if x.id == sid),
                    "at_s": round(t, 1),
                    "png": base64.b64encode(png).decode(),
                }
                for sid, t, png in zip(want, times, pngs, strict=True)
            ],
            "unknown": unknown,
            "player_issues": _dicts(s.take_player_issues()),
        }

    async def export(self, handle: str, path: str = "", format: str = "mp4", max_mb: float = 0, wait_s: float | None = None) -> dict:
        """Write the walkthrough to a file the agent can attach: an MP4, or one HTML page that plays
        offline. A relative or empty `path` is in the project folder. Waits for the narration first."""
        import asyncio

        from .export import export_html, timeline_total
        from .video import render_mp4

        wait_s = EXPORT_WAIT_S if wait_s is None else wait_s
        s = self.store.by_handle(handle)
        if format not in ("mp4", "html"):
            raise ValueError("format must be mp4 or html")
        out = export_path(s.w.title, s.w.root, path, format)
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        if not says:
            raise ValueError("nothing to export yet: the walkthrough has no narration")
        t0 = time.monotonic()
        while any(x.status == "pending" for x in says) and time.monotonic() - t0 < wait_s:
            await asyncio.sleep(0.5)
        pending = sum(1 for x in says if x.status == "pending")
        if pending:
            raise ValueError(f"{pending} paragraph(s) are still being voiced after {wait_s:.0f} s; try again shortly")
        silent = sum(1 for x in says if x.status != "ready")
        s.save_now()  # the exporters read the saved state
        data_dir = self.store.data_dir
        try:
            if format == "mp4":
                r = await asyncio.to_thread(render_mp4, data_dir, s.w.id, out, quiet=True, max_bytes=int(max_mb * 1e6))
                shrunk, errors = r["shrunk"], r["errors"]
            else:
                await asyncio.to_thread(export_html, data_dir, s.w.id, out)
                shrunk, errors = "", []
                size = out.stat().st_size
                if max_mb and size > max_mb * 1e6:
                    out.unlink()
                    raise SystemExit(f"the page is {size / 1e6:.1f} MB, over the {max_mb:g} MB limit; export an MP4 instead")
        except SystemExit as e:  # the exporters stop the command line this way
            raise ValueError(str(e)) from None
        return {
            "path": str(out),
            "format": format,
            "bytes": out.stat().st_size,
            "duration_s": round(timeline_total(s.w), 1),
            "shrunk": shrunk,
            "silent": silent,
            "errors": errors,
            "seconds": round(time.monotonic() - t0, 1),
        }

    async def source(self, wid: str) -> str:
        s = self.store.get(wid)
        if s is None:
            raise ValueError(f"no walkthrough {wid}")
        return s.source


class RemoteService:
    """Forwards calls to the daemon's `/api/rpc/{method}` route."""

    def __init__(self, base: str, token: str) -> None:
        import httpx

        self.base = base.rstrip("/")
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=5.0), headers={"Authorization": f"Bearer {token}"})

    async def _call(self, method: str, http_timeout: float | None = None, **kw) -> Any:
        extra = {"timeout": http_timeout} if http_timeout else {}
        r = await self.client.post(f"{self.base}/api/rpc/{method}", json=kw, **extra)
        try:
            data = r.json()
        except ValueError:  # a plain-text refusal, or not the daemon at all
            data = {"error": f"daemon error {r.status_code}: {r.text.strip()[:200]}"}
        if r.status_code != 200:
            raise ValueError(data.get("error", f"daemon error {r.status_code}"))
        return data["result"]

    async def start(self, title, frame, voice, speed, project_dir, theme=""):
        return await self._call("start", title=title, frame=frame, voice=voice, speed=speed, project_dir=project_dir, theme=theme)

    async def add(self, handle, markdown):
        return await self._call("add", handle=handle, markdown=markdown)

    async def playable(self, handle, timeout, give_up=False):
        return await self._call("playable", handle=handle, timeout=timeout, give_up=give_up)

    async def revise(self, handle, slide, markdown):
        return await self._call("revise", handle=handle, slide=slide, markdown=markdown)

    async def finish(self, handle, questions, check=True):
        if check:  # a daemon from before `check` still takes the call
            return await self._call("finish", handle=handle, questions=questions)
        return await self._call("finish", handle=handle, questions=questions, check=False)

    async def list(self):
        return await self._call("list")

    async def source(self, wid):
        return await self._call("source", wid=wid)

    async def preview(self, handle, slides=None, short=360):
        return await self._call("preview", handle=handle, slides=slides, short=short)

    async def export(self, handle, path="", format="mp4", max_mb=0):
        # Rendering takes a while: about a tenth of the video's length, plus waiting for the voice.
        return await self._call("export", http_timeout=EXPORT_WAIT_S + 900, handle=handle, path=path, format=format, max_mb=max_mb)


RPC_METHODS = {"start", "add", "playable", "revise", "finish", "list", "source", "preview", "export"}
EXPORT_WAIT_S = 300.0  # for the narration to finish voicing before an export
PREVIEW_MAX = 12
LAYOUT_CHECK_MAX = 60  # slides drawn by the finish-time layout check
LAYOUT_CHECK_S = 25.0


def _end_times(w) -> dict[str, float]:
    """Slide id -> a moment just before the slide ends, when everything on it is revealed."""
    from .export import timeline, timeline_total

    starts: dict[str, float] = {}
    for t, slide, _ in timeline(w):
        starts.setdefault(slide.id, t)
    order = [sl.id for sl in w.slides if sl.id in starts]
    total = timeline_total(w)
    out = {}
    for i, sid in enumerate(order):
        end = starts[order[i + 1]] if i + 1 < len(order) else total
        out[sid] = max(starts[sid], end - 0.35)
    return out


def export_path(title: str, root: str | None, path: str, format: str) -> Path:
    """Where an export goes: `path` (relative ones inside the project folder), else a file named
    after the title in the project folder. The suffix must match the format."""
    import re

    if path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            if not root:
                raise ValueError("a relative path needs a project folder; pass an absolute path")
            p = Path(root) / p
    else:
        if not root:
            raise ValueError("no project folder to save in; pass an absolute path")
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].strip("-") or "walkthrough"
        p = Path(root) / f"{slug}.{format}"
    p = p.resolve()
    want = (".mp4",) if format == "mp4" else (".html", ".htm")
    if p.suffix.lower() not in want:
        raise ValueError(f"path must end in {want[0]} for format {format}")
    if p.is_dir():
        raise ValueError(f"{p} is a folder")
    if not p.parent.is_dir():
        raise ValueError(f"folder {p.parent} does not exist")
    return p


def mmss(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def now() -> float:
    return time.time()
