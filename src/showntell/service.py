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
    def __init__(self, store: Store) -> None:
        self.store = store

    def speech_status(self) -> dict:
        n = self.store.narrator
        return {"ready": n.ready, "state": n.status.state, "describe": n.status.describe()}

    async def start(self, title: str, frame: str, voice: str, speed: float, project_dir: str | None) -> dict:
        root = None
        if project_dir:
            p = Path(project_dir).expanduser()
            if not p.is_dir():
                raise ValueError(f"project_dir {project_dir} is not a folder")
            root = str(p.resolve())
        s = self.store.create(title, frame, voice, speed, root)
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

    async def finish(self, handle: str, questions: list[str] | None) -> dict:
        s = self.store.by_handle(handle)
        issues = s.finish(questions) + s.take_late_issues()
        says = [x for sl in s.w.slides for x in sl.says if x.text]
        outline, t = [], 0.0
        for sl in s.w.slides:
            outline.append({"id": sl.id, "title": sl.title, "start_s": round(t, 1)})
            for x in sl.says:
                t += x.pause or (x.duration or len(x.text.split()) / 2.7) + 0.3
            t += 0.3
        return {
            "outline": outline,
            "url": s.url,
            "slides": len(s.w.slides),
            "duration_s": round(sum(x.duration or len(x.text.split()) / 2.7 for x in says)
                                + sum(x.pause for sl in s.w.slides for x in sl.says), 1),
            "pending": sum(1 for x in says if x.status == "pending"),
            "issues": _dicts(issues),
            "player_issues": _dicts(s.take_player_issues()),
            "questions": s.w.questions,
        }

    async def list(self) -> list[dict]:
        return self.store.list(20)

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
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(70.0, connect=5.0),
                                        headers={"Authorization": f"Bearer {token}"})

    async def _call(self, method: str, **kw) -> Any:
        r = await self.client.post(f"{self.base}/api/rpc/{method}", json=kw)
        data = r.json()
        if r.status_code != 200:
            raise ValueError(data.get("error", f"daemon error {r.status_code}"))
        return data["result"]

    async def start(self, title, frame, voice, speed, project_dir):
        return await self._call("start", title=title, frame=frame, voice=voice, speed=speed, project_dir=project_dir)

    async def add(self, handle, markdown):
        return await self._call("add", handle=handle, markdown=markdown)

    async def playable(self, handle, timeout, give_up=False):
        return await self._call("playable", handle=handle, timeout=timeout, give_up=give_up)

    async def revise(self, handle, slide, markdown):
        return await self._call("revise", handle=handle, slide=slide, markdown=markdown)

    async def finish(self, handle, questions):
        return await self._call("finish", handle=handle, questions=questions)

    async def list(self):
        return await self._call("list")

    async def source(self, wid):
        return await self._call("source", wid=wid)


RPC_METHODS = {"start", "add", "playable", "revise", "finish", "list", "source"}


def mmss(seconds: float | None) -> str:
    s = int(round(seconds or 0))
    return f"{s // 60}:{s % 60:02d}"


def now() -> float:
    return time.time()
