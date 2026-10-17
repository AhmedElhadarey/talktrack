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
        from .export import slide_starts, timeline_total

        s = self.store.by_handle(handle)
        layout = self.layout_check and check
        unchecked = await self._check_layout(s) if layout else ""
        issues = s.finish(questions, unchecked=unchecked if layout else None) + s.take_late_issues()
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        starts = slide_starts(s.w)
        return {
            "outline": [{"id": sl.id, "title": sl.title, "start_s": round(starts[sl.id], 1)} for sl in s.w.slides],
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

    async def _call(self, method: str, **kw) -> Any:
        r = await self.client.post(f"{self.base}/api/rpc/{method}", json=kw)
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


RPC_METHODS = {"start", "add", "playable", "revise", "finish", "list", "source", "preview"}
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


def mmss(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def now() -> float:
    return time.time()
