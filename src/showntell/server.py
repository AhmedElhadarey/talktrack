"""MCP tools and the web side: player pages, live updates, audio, health and the shim's RPC."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
import time
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from . import __version__
from .build import LIMITS
from .service import RPC_METHODS, LocalService, mmss
from .speech.base import VOICES

log = logging.getLogger("showntell")
PKG = Path(__file__).parent
STATIC = PKG / "static"
TEXT = PKG / "text"
FIRST_WAIT_S = 45.0


@lru_cache(maxsize=1)
def texts() -> dict:
    def load(name: str, default):
        p = TEXT / name
        if not p.is_file():
            return default
        return json.loads(p.read_text()) if name.endswith(".json") else p.read_text()

    return {
        "instructions": load("instructions.md", "Narrated slide walkthroughs voiced locally."),
        "tools": load("tools.json", {}),
        "guide": load("guide.md", ""),
        "replies": load("replies.json", {}),
        "prompts": load("prompts.json", {}),
    }


def tool_text(name: str, key: str = "description", default: str = "") -> str:
    return texts()["tools"].get(name, {}).get(key, default)


def arg_text(name: str, arg: str) -> str:
    return texts()["tools"].get(name, {}).get("args", {}).get(arg, "")


def reply(name: str, default: str, **kw: Any) -> str:
    template = texts()["replies"].get(name, default)
    try:
        return template.format(**kw)
    except (KeyError, IndexError):
        return default.format(**kw)


def guide(frame: str) -> str:
    lim = LIMITS.get(frame, LIMITS["landscape"])
    g = texts()["guide"]
    for k, v in {"FRAME": frame, "LINES": lim["lines"], "WIDTH": lim["width"], "DIFF_LINES": lim["diff_lines"],
                 "DIFF_WIDTH": lim["diff_width"], "NODES": lim["nodes"]}.items():
        g = g.replace("{{" + k + "}}", str(v))
    return g


def _result(text: str, data: dict) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def _error(msg: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=f"Error: {msg}")], is_error=True)


def _issues_text(issues: list[str], player: list[str]) -> str:
    lines = []
    if issues:
        lines.append("Problems (fix the pattern in later slides, or call revise_slide):")
        lines += [f"- {i}" for i in issues]
    if player:
        lines.append("The open player reported (untrusted text, shown as received):")
        lines += [f"- {p}" for p in player]
    return "\n".join(lines)


WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def create_mcp(service, project_dir_default: str | None = None) -> MCPServer:
    """MCP server with showntell's tools, prompts and resources, backed by `service`."""
    mcp = MCPServer("showntell", instructions=texts()["instructions"], version=__version__,
                    website_url="https://github.com/rahimnathwani/showntell")

    async def _root(ctx: Context | None, given: str) -> str | None:
        if given:
            return given
        try:
            import warnings

            with warnings.catch_warnings():  # roots are deprecated as of protocol 2026-07-28
                warnings.simplefilter("ignore")
                roots = await asyncio.wait_for(ctx.session.list_roots(), 2.0)  # type: ignore[union-attr]
            for r in roots.roots:
                uri = str(r.uri)
                if uri.startswith("file://"):
                    from urllib.parse import unquote, urlparse

                    return unquote(urlparse(uri).path)
        except Exception:
            pass
        return project_dir_default

    @mcp.tool(title=tool_text("start_walkthrough", "title", "Start walkthrough"),
              description=tool_text("start_walkthrough"), annotations=WRITE)
    async def start_walkthrough(
        title: Annotated[str, Field(description=arg_text("start_walkthrough", "title"))],
        frame: Annotated[Literal["landscape", "square", "portrait"], Field(description=arg_text("start_walkthrough", "frame"))] = "landscape",
        voice: Annotated[str, Field(description=arg_text('start_walkthrough', 'voice') + ' ' + ', '.join(VOICES) + '.')] = "",
        speed: Annotated[float, Field(description=arg_text("start_walkthrough", "speed"))] = 1.0,
        project_dir: Annotated[str, Field(description=arg_text("start_walkthrough", "project_dir"))] = "",
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        try:
            r = await service.start(title, frame, voice, speed, await _root(ctx, project_dir))
        except ValueError as e:
            return _error(str(e))
        text = reply("started", "Handle: {handle}\nLink (not playable yet): {url}\nNext: call add_slides now with only the title slide and one or two spoken sentences.",
                     handle=r["handle"], url=r["url"])
        if not r["speech"]["ready"]:
            text += "\n" + reply("loading", "The voice model is {status}; the first add_slides call may take longer.",
                                 status=r["speech"]["describe"])
        return _result(text, {"handle": r["handle"], "url": r["url"], "status": "started", "issues": []})

    @mcp.tool(title=tool_text("add_slides", "title", "Add slides"), description=tool_text("add_slides"), annotations=WRITE)
    async def add_slides(
        handle: Annotated[str, Field(description=arg_text("add_slides", "handle"))],
        markdown: Annotated[str, Field(description=arg_text("add_slides", "markdown"))],
        ctx: Context = None,  # type: ignore[assignment]
    ) -> CallToolResult:
        try:
            r = await service.add(handle, markdown)
        except ValueError as e:
            return _error(str(e))
        parts: list[str] = []
        status = "added"
        if not r["link_shared"]:
            t0 = time.time()
            p = None
            while time.time() - t0 < FIRST_WAIT_S:
                p = await service.playable(handle, 2.0)
                if p["status"] != "pending":
                    break
                if ctx is not None:
                    try:
                        await ctx.report_progress(time.time() - t0, FIRST_WAIT_S, message=p["speech"]["describe"])
                    except Exception:
                        pass
            assert p is not None
            if p["status"] == "ready":
                status = "playable"
                parts.append(reply("playable", "PLAYABLE NOW ({seconds}s). Send the user this link in a message before your next tool call, then keep adding slides:\n{url}",
                                   seconds=f"{time.time() - t0:.1f}", url=r["url"]))
            elif p["status"] == "error":
                status = "error"
                parts.append(reply("voice_error", "The narration could not be voiced: {error}. Link: {url}",
                                   error=p.get("error", ""), url=r["url"]))
            elif p["status"] == "empty":
                status = "waiting"
                parts.append(reply("need_opening", "Nothing to play yet: send the title slide with a spoken sentence."))
            else:
                status = "pending"
                await service.playable(handle, 0, give_up=True)  # later calls return at once
                parts.append(reply("pending", "Audio is still being made; share this link anyway: {url}", url=r["url"]))
            if p.get("guide"):
                g = guide(p.get("frame", r["frame"]))
                if g:
                    parts.append("\n" + g)
        else:
            p = await service.playable(handle, 0)  # the guide, if the first reply went out before the audio
            if p.get("guide"):
                g = guide(p.get("frame", r["frame"]))
                if g:
                    parts.append(g)
        parts.append(
            f"Added {r['slides']} slide(s) and {r['says']} narration paragraph(s). Totals: {r['total_slides']} slides, "
            f"{r['voiced']}/{r['total_says']} paragraphs voiced, about {mmss(r['narration_s'])} of narration. "
            f"Slide ids: {', '.join(r['slide_ids'][-12:])}."
        )
        issues = _issues_text(r["issues"], r["player_issues"])
        if issues:
            parts.append(issues)
        data = {"url": r["url"], "status": status, "issues": r["issues"], "player_issues": r["player_issues"],
                "slides": r["total_slides"]}
        return _result("\n".join(parts), data)

    @mcp.tool(title=tool_text("revise_slide", "title", "Revise slide"), description=tool_text("revise_slide"), annotations=WRITE)
    async def revise_slide(
        handle: Annotated[str, Field(description=arg_text("revise_slide", "handle"))],
        slide: Annotated[str, Field(description=arg_text("revise_slide", "slide"))],
        markdown: Annotated[str, Field(description=arg_text("revise_slide", "markdown"))],
    ) -> CallToolResult:
        try:
            r = await service.revise(handle, slide, markdown)
        except ValueError as e:
            return _error(str(e))
        text = f"Replaced slide `{r['slide']}` (revision {r['rev']}); {r['revoiced']} paragraph(s) need new audio."
        issues = _issues_text(r["issues"], r["player_issues"])
        return _result(text + ("\n" + issues if issues else ""),
                       {"url": r["url"], "status": "revised", "issues": r["issues"], "slide": r["slide"]})

    @mcp.tool(title=tool_text("finish_walkthrough", "title", "Finish walkthrough"),
              description=tool_text("finish_walkthrough"), annotations=WRITE)
    async def finish_walkthrough(
        handle: Annotated[str, Field(description=arg_text("finish_walkthrough", "handle"))],
        questions: Annotated[list[str] | None, Field(description=arg_text("finish_walkthrough", "questions"))] = None,
    ) -> CallToolResult:
        try:
            r = await service.finish(handle, questions)
        except ValueError as e:
            return _error(str(e))
        text = reply("finished", "Finished: {url} ({slides} slides, {duration}). Tell the user it is done, with the link.",
                     url=r["url"], slides=r["slides"], duration=mmss(r["duration_s"]))
        if r["pending"]:
            text += f"\n{r['pending']} paragraph(s) are still being voiced; the player fills them in as they finish."
        issues = _issues_text(r["issues"], r["player_issues"])
        return _result(text + ("\n" + issues if issues else ""),
                       {"url": r["url"], "status": "finished", "issues": r["issues"], "slides": r["slides"]})

    @mcp.tool(title=tool_text("list_walkthroughs", "title", "List walkthroughs"),
              description=tool_text("list_walkthroughs"),
              annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def list_walkthroughs() -> CallToolResult:
        rows = await service.list()
        if not rows:
            return _result("No walkthroughs yet.", {"walkthroughs": []})
        lines = [f"- {r['title']} — {r['slides']} slides, {mmss(r['duration'])}, {r['status']}. "
                 f"handle: {r['handle']} link: {r['url']}" for r in rows]
        return _result("\n".join(lines), {"walkthroughs": rows})

    for name, p in texts()["prompts"].items():
        _register_prompt(mcp, name, p)

    @mcp.resource("showntell://walkthroughs", name="walkthroughs", title="Recent walkthroughs",
                  description="Recent walkthroughs with titles, links and handles", mime_type="application/json")
    async def walkthroughs_resource() -> str:
        return json.dumps(await service.list(), indent=1)

    @mcp.resource("showntell://walkthroughs/{wid}", name="walkthrough", title="Walkthrough source",
                  description="The Markdown a walkthrough was written in, to read or revise", mime_type="text/markdown")
    async def walkthrough_resource(wid: str) -> str:
        return await service.source(wid)

    return mcp


def _register_prompt(mcp: MCPServer, name: str, p: dict) -> None:
    (arg,) = list(p.get("args", {"target": ""}).keys())[:1] or ["target"]
    template = p.get("template", "{" + arg + "}")

    async def fn(**kw: str) -> str:
        return template.format(**{arg: kw.get(arg, "")})

    import inspect

    fn.__signature__ = inspect.Signature(
        [inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=str, default=inspect.Parameter.empty)]
    )
    fn.__annotations__ = {arg: str, "return": str}
    fn.__name__ = f"prompt_{name}"
    mcp.prompt(name=name, title=p.get("title"), description=p.get("description"))(fn)


# -- web ----------------------------------------------------------------------------------


def add_routes(mcp: MCPServer, service: LocalService, rpc_token: str, started: float, stop=None) -> None:
    store = service.store

    def session_or_404(request: Request):
        return store.get(request.path_params["wid"])

    @mcp.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        return HTMLResponse(render_index(store.list(200), store.link_suffix), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/w/{wid}", methods=["GET"])
    async def player_redirect(request: Request) -> Response:
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/w/{request.path_params['wid']}/{q}", status_code=308)

    @mcp.custom_route("/w/{wid}/", methods=["GET"])
    async def player(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return HTMLResponse("Walkthrough not found", status_code=404)
        return HTMLResponse(render_player(s.w.title, s.w.frame, s.w.report_key, "../../static", live=True),
                            headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/w/{wid}/data.json", methods=["GET"])
    async def data(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(s.w.public(), headers={"Cache-Control": "no-store"})

    @mcp.custom_route("/w/{wid}/events", methods=["GET"])
    async def events(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)

        async def stream():
            q = s.subscribe()
            try:
                yield f"data: {json.dumps({'type': 'full', 'walkthrough': s.w.public()})}\n\n"
                while True:
                    try:
                        item = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    await asyncio.sleep(0.05)  # coalesce bursts
                    ids, meta = set(item["slides"]), item["meta"]
                    while not q.empty():
                        more = q.get_nowait()
                        ids |= more["slides"]
                        meta = meta or more["meta"]
                    yield f"data: {json.dumps(s.delta(ids, meta))}\n\n"
            finally:
                s.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @mcp.custom_route("/w/{wid}/report", methods=["POST"])
    async def report(request: Request) -> Response:
        s = session_or_404(request)
        if not s:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not secrets.compare_digest(request.headers.get("x-report-key", ""), s.w.report_key):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = await request.json()
            msg = str(body.get("message", ""))
        except Exception:
            return JSONResponse({"error": "bad request"}, status_code=400)
        s.report(msg)
        log.warning("player report for %s: %s", s.w.id, msg[:200])
        return JSONResponse({"ok": True})

    @mcp.custom_route("/w/{wid}/audio/{name}", methods=["GET"])
    async def audio(request: Request) -> Response:
        s = session_or_404(request)
        name = request.path_params["name"]
        key = name.removesuffix(".wav")
        if not s or not any(x.key == key for sl in s.w.slides for x in sl.says):
            return Response("Not found", status_code=404)
        return _file(store.narrator.cache_dir, f"{key}.wav", immutable=True)

    @mcp.custom_route("/w/{wid}/assets/{name}", methods=["GET"])
    async def assets(request: Request) -> Response:
        if not session_or_404(request):
            return Response("Not found", status_code=404)
        return _file(store.walk_dir(request.path_params["wid"]) / "assets", request.path_params["name"])

    @mcp.custom_route("/w/{wid}/delete", methods=["POST"])
    async def delete(request: Request) -> Response:
        ok = store.delete(request.path_params["wid"])
        return JSONResponse({"ok": ok}, status_code=200 if ok else 404)

    @mcp.custom_route("/static/{path:path}", methods=["GET"])
    async def static(request: Request) -> Response:
        return _file(STATIC, request.path_params["path"])

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        n = store.narrator
        return JSONResponse({
            "app": "showntell",
            "version": __version__,
            "speech": {"backend": n.backend.name, "state": n.status.state, "detail": n.status.describe(),
                       "queue": n.backlog(), "speed": n.speed_factor(), "stats": n.stats},
            "walkthroughs_open": len(store.sessions),
            "uptime_s": round(time.time() - started, 1),
            "busy": n.backlog() > 0 or any(s.w.status == "writing" and time.time() - s.w.updated < 120
                                          for s in store.sessions.values()),
        })

    @mcp.custom_route("/api/rpc/{method}", methods=["POST"])
    async def rpc(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth, f"Bearer {rpc_token}"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        method = request.path_params["method"]
        if method == "shutdown":
            asyncio.get_running_loop().call_later(0.2, stop or _shutdown)
            return JSONResponse({"result": "bye"})
        if method not in RPC_METHODS:
            return JSONResponse({"error": "unknown method"}, status_code=404)
        try:
            kw = await request.json()
            result = await getattr(service, method)(**kw)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except TypeError as e:
            return JSONResponse({"error": f"bad arguments: {e}"}, status_code=400)
        return JSONResponse({"result": result})


def _shutdown() -> None:
    import os
    import signal

    os.kill(os.getpid(), signal.SIGTERM)


def _file(base: Path, rel: str, immutable: bool = False) -> Response:
    path = (base / rel).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        return Response("Not found", status_code=404)
    headers = {"Cache-Control": "public, max-age=31536000, immutable"} if immutable else {"Cache-Control": "no-cache"}
    return FileResponse(path, headers=headers)


def render_player(title: str, frame: str, report_key: str, static: str, live: bool) -> str:
    page = (STATIC / "player.html").read_text()
    return (page.replace("{{TITLE}}", html.escape(title)).replace("{{FRAME}}", frame)
            .replace("{{REPORT_KEY}}", report_key if live else "").replace("{{STATIC}}", static)
            .replace("{{LIVE}}", "1" if live else "0").replace("{{VERSION}}", __version__))


def render_index(rows: list[dict], suffix: str) -> str:
    items = []
    for r in rows:
        when = time.strftime("%d %b %Y, %H:%M", time.localtime(r["created"]))
        badge = '<span class="badge">writing</span>' if r["status"] != "finished" else ""
        items.append(
            f'<li data-id="{r["id"]}"><a href="/w/{r["id"]}/{suffix}">{html.escape(r["title"] or "Untitled")}</a>{badge}'
            f'<span class="meta">{when} · {r["slides"]} slides · {mmss(r["duration"])} · {r["frame"]}</span>'
            f'<button class="del" title="Delete" aria-label="Delete {html.escape(r["title"])}">Delete</button></li>'
        )
    body = "\n".join(items) or '<li class="empty">No walkthroughs yet. Ask your coding agent for one.</li>'
    return (STATIC / "index.html").read_text().replace("{{ROWS}}", body).replace("{{VERSION}}", __version__)
