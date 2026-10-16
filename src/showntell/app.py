"""Assemble and run the daemon: speech worker, store, MCP over HTTP, web routes and the guard."""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from . import daemon
from .config import Config
from .server import add_routes, create_mcp
from .service import LocalService
from .speech import Narrator, Status, make_backend
from .store import Store

log = logging.getLogger("showntell")
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}


class Guard:
    """Host and Origin checks on every route, JSON-only POSTs, and an access token when the
    daemon is reachable from other machines."""

    def __init__(self, app, cfg: Config) -> None:
        self.app = app
        self.allowed = set(LOOPBACK_NAMES)
        if cfg.public_url:
            self.allowed.add((urlparse(cfg.public_url).hostname or "").lower())
        if not cfg.loopback and cfg.host not in ("0.0.0.0", "::"):
            self.allowed.add(cfg.host.lower())
        self.open_hosts = not cfg.loopback and not cfg.public_url  # bound wide without a known name
        self.token = cfg.access_token if not cfg.loopback else ""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").lower()
        hostname = host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
        if hostname not in self.allowed and not self.open_hosts:
            return await _deny(send, 421, "unknown host")
        path = scope.get("path", "")
        client = (scope.get("client") or ("", 0))[0]
        local_client = client in ("127.0.0.1", "::1", "localhost") or client.startswith("127.")
        if path.startswith(("/mcp", "/api/rpc")) and not local_client:
            return await _deny(send, 403, "local clients only")
        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            origin = headers.get("origin")
            if origin and origin != "null":
                oh = (urlparse(origin).hostname or "").lower()
                if oh not in {h.strip("[]") for h in self.allowed} and not (self.open_hosts and oh == hostname):
                    return await _deny(send, 403, "cross-origin request")
            if path.startswith(("/w/", "/api/rpc")) and not headers.get("content-type", "").startswith("application/json"):
                return await _deny(send, 415, "JSON only")
        if self.token and not path.startswith(("/mcp", "/api/rpc", "/health")):
            q = parse_qs(scope.get("query_string", b"").decode())
            given = (q.get("t") or [""])[0]
            cookie = SimpleCookie(headers.get("cookie", "")).get("showntell_t")
            ok_query = given and secrets.compare_digest(given, self.token)
            ok_cookie = cookie and secrets.compare_digest(cookie.value, self.token)
            if not (ok_query or ok_cookie):
                return await _deny(send, 401, "access token required: open the link you were given")
            if ok_query and not ok_cookie:
                async def send_with_cookie(message):
                    if message["type"] == "http.response.start":
                        message.setdefault("headers", [])
                        message["headers"] = list(message["headers"]) + [
                            (b"set-cookie", f"showntell_t={self.token}; Path=/; HttpOnly; SameSite=Strict".encode())
                        ]
                    await send(message)

                return await self.app(scope, receive, send_with_cookie)
        return await self.app(scope, receive, send)


async def _deny(send, status: int, text: str) -> None:
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def pick_port(host: str, preferred: int, tries: int = 20) -> int:
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"no free port between {preferred} and {preferred + tries - 1}")


def serve(cfg: Config) -> None:
    started = time.time()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    other = daemon.running(cfg)
    if other:
        raise SystemExit(f"showntell is already running at {other['url']} (pid {other['pid']})")
    if not cfg.loopback and not cfg.access_token:
        cfg.access_token = secrets.token_urlsafe(16)
    port = pick_port(cfg.host, cfg.port)
    if port != cfg.port:
        log.info("port %d is taken; using %d", cfg.port, port)
        cfg.port = port
    status = Status()
    narrator = Narrator(make_backend(cfg.speech, cfg.data_dir, status), cfg.audio_dir, status)
    narrator.lexicon = load_lexicon(cfg)
    narrator.start()
    suffix = f"?t={cfg.access_token}" if cfg.access_token else ""
    store = Store(cfg.data_dir, cfg.base_url, narrator, default_voice=cfg.voice, open_browser=cfg.open_browser,
                  link_suffix=suffix)
    service = LocalService(store)
    mcp = create_mcp(service)
    rpc_token = secrets.token_urlsafe(24)
    add_routes(mcp, service, rpc_token, started)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )
    app = Guard(mcp.streamable_http_app(streamable_http_path="/mcp", transport_security=security, host=cfg.host), cfg)

    async def main() -> None:
        store.loop = asyncio.get_running_loop()
        config = uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning", lifespan="on")
        server = uvicorn.Server(config)
        daemon.write_lock(cfg, rpc_token)
        log.info("showntell %s at %s (MCP: http://localhost:%d/mcp, speech: %s)",
                 _version(), cfg.base_url + "/" + suffix, cfg.port, narrator.backend.name)
        try:
            await server.serve()
        finally:
            daemon.clear_lock(cfg)
            for s in store.sessions.values():
                s.save_now()

    asyncio.run(main())


def load_lexicon(cfg: Config) -> dict[str, str]:
    """User word list: one `word = spoken form` per line."""
    out: dict[str, str] = {}
    if cfg.lexicon_file.is_file():
        for line in cfg.lexicon_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip()
    return out


def _version() -> str:
    from . import __version__

    return __version__
