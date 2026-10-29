"""Reading what the user wants explained: a web page, a PDF, an image or a text file.

The text comes back as written, not summarised, so a walkthrough rests on what the source says.
It is someone else's text: the server's reply marks it as material to explain, not instructions.
A link is fetched only from public addresses, and every redirect is checked again, so a page
cannot point talktrack at the machine it runs on or at the local network. The name is resolved
once, the address checked, and the connection made to that same address (with the name as the
Host header and for TLS), so a host that changes its answer after the check is not followed.
Local files must be inside the project folder, as for slides. Parsing runs in a worker thread,
so a large page or PDF never holds up the daemon's other work.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import __version__, sources

MAX_BYTES = 10 * 1024 * 1024  # a download or a file
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CHARS = 60_000  # text in one reply, about 10,000 words; longer sources come in pieces
MAX_REDIRECTS = 5
TIMEOUT_S = 20
MAX_OUTLINE = 200
MAX_TABLE_ROWS = 60
MAX_IMAGES = 30
MAX_DEPTH = 256  # deeper HTML is flattened, so rendering never recurses without bound
MAX_TITLE = 200
# IPv6 ranges that carry an IPv4 address in their last 32 bits, which is the one reached
EMBEDS_V4 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("::/96"))

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
TEXT_TYPES = {".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown", ".rst": "text/plain",
              ".csv": "text/plain", ".html": "text/html", ".htm": "text/html"}  # fmt: skip
HTML_TYPES = {"text/html", "application/xhtml+xml"}


class ReadError(Exception):
    pass


@dataclass
class Material:
    kind: str  # "page", "pdf", "image" or "text"
    title: str
    origin: str  # the link or path as read
    text: str = ""  # Markdown
    image: bytes = b""
    mime: str = ""
    slide_src: str = ""  # what an image slide's ![](...) takes
    pages: int = 0  # PDF: pages in the file
    shown: tuple[int, int] | None = None  # PDF: the pages in `text`
    asked_end: int = 0  # PDF: the last page asked for, which the text budget may have cut short
    no_text: bool = False  # PDF: none of the pages shown has a text layer
    outline: list[tuple[int, int, str]] = field(default_factory=list)  # PDF: (depth, page, title)
    part: int = 1
    parts: int = 1
    words: int = 0  # in `text`


async def read(
    source: str, root: Path | None, pages: str = "", part: int = 1, transport: httpx.AsyncBaseTransport | None = None
) -> Material:
    """Read `source`: an http(s) link, or a path inside `root`."""
    s = source.strip()
    if not s:
        raise ReadError("source is empty: give a link or a file path")
    if re.match(r"^[a-z][a-z0-9+.-]*:", s, re.I) and not re.match(r"^[a-z]:[\\/]", s, re.I):
        url, ctype, data = await fetch(s, transport)
        return await asyncio.to_thread(from_bytes, data, ctype, url, pages, part, url)
    try:
        path = sources.resolve(root, s)
        size = path.stat().st_size
        if size > MAX_BYTES:
            raise ReadError(f"{s} is {size // (1024 * 1024)} MB; files up to {MAX_BYTES // (1024 * 1024)} MB are read")
        data = path.read_bytes()
    except sources.SourceError as e:
        hint = " Copy it into the project folder (for example with cp) and read it from there." if "outside the project" in str(e) else ""
        raise ReadError(f"{e}.{hint}") from None
    except OSError as e:
        raise ReadError(f"cannot read {s}: {e.strerror or e}") from None
    suffix = path.suffix.lower()
    ctype = IMAGE_TYPES.get(suffix) or TEXT_TYPES.get(suffix) or ("application/pdf" if suffix == ".pdf" else "")
    return await asyncio.to_thread(from_bytes, data, ctype, s, pages, part, s)


# -- fetching ---------------------------------------------------------------------------------


async def fetch(url: str, transport: httpx.AsyncBaseTransport | None = None) -> tuple[str, str, bytes]:
    """GET `url` from a public address: (final url, content type, body)."""
    headers = {
        "User-Agent": f"talktrack/{__version__} (+https://github.com/AhmedElhadarey/talktrack)",
        "Accept": "text/html,application/xhtml+xml,application/pdf,image/*,text/*;q=0.9,*/*;q=0.5",
    }
    # No proxies from the environment: the connection must go to the address that was checked.
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=TIMEOUT_S, transport=transport, headers=headers, trust_env=False
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            pinned, host, ext = await pin(url)
            try:
                async with client.stream("GET", pinned, headers={"Host": host}, extensions=ext) as r:
                    if r.is_redirect and r.headers.get("location"):
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code >= 400:
                        raise ReadError(f"{url} answered {r.status_code} {r.reason_phrase}")
                    size = r.headers.get("content-length", "")
                    if size.isdigit() and int(size) > MAX_BYTES:
                        raise ReadError(f"{url} is {int(size) // (1024 * 1024)} MB; up to {MAX_BYTES // (1024 * 1024)} MB is read")
                    body = bytearray()
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise ReadError(f"{url} is larger than {MAX_BYTES // (1024 * 1024)} MB, the most that is read")
                    return url, r.headers.get("content-type", ""), bytes(body)
            except (httpx.HTTPError, httpx.InvalidURL) as e:
                raise ReadError(f"could not fetch {url}: {e or type(e).__name__}") from None
        raise ReadError(f"{url}: more than {MAX_REDIRECTS} redirects")


async def pin(url: str) -> tuple[httpx.URL, str, dict]:
    """Check `url` and resolve its host once: (the URL with the checked address as its host, the
    Host header, the request extensions that keep TLS checking the certificate for the name)."""
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL as e:
        raise ReadError(f"{url} is not a valid link: {e}") from None
    if u.scheme not in ("http", "https"):
        raise ReadError(f"only http and https links are read, not {u.scheme or 'this'}:")
    if u.userinfo:
        raise ReadError("links with a user name or password in them are not read")
    host = u.raw_host.decode("ascii").lower()  # the IDNA form httpx itself would connect to
    if not host:
        raise ReadError(f"{url} has no host name")
    addresses = await _addresses(host, u.port or (443 if u.scheme == "https" else 80))
    for a in addresses:
        if not is_public(a):
            raise ReadError(f"{host} is a private or local address ({a}); only public web pages are read")
    named = f"[{host}]" if ":" in host else host
    return (
        u.copy_with(host=addresses[0]),
        named if u.port is None else f"{named}:{u.port}",
        {"sni_hostname": host} if u.scheme == "https" else {},
    )


def is_public(address: str) -> bool:
    """Whether `address` and any IPv4 address it carries are on the public internet."""
    ip = ipaddress.ip_address(address)
    seen: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        seen += [x for x in (ip.ipv4_mapped, ip.sixtofour, *(ip.teredo or ())) if x is not None]
        if any(ip in net for net in EMBEDS_V4) and int(ip) & 0xFFFFFFFF:
            seen.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return all(x.is_global for x in seen)


async def check_public(url: str) -> None:
    await pin(url)


async def _addresses(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as e:
        raise ReadError(f"cannot find {host}: {getattr(e, 'strerror', None) or e}") from None
    found = sorted({str(i[4][0]).split("%")[0] for i in infos}, key=lambda a: (":" in a, a))  # IPv4 first
    if not found:
        raise ReadError(f"cannot find {host}")
    return found


# -- by content type ----------------------------------------------------------------------------


def from_bytes(data: bytes, ctype: str, origin: str, pages: str = "", part: int = 1, slide_src: str = "") -> Material:
    mime = ctype.split(";")[0].strip().lower()
    name = Path(urlparse(origin).path).name or urlparse(origin).hostname or origin
    if mime == "application/pdf" or data[:5] == b"%PDF-":
        if part != 1:
            raise ReadError("part= is for web pages and text files; read a PDF in stretches with pages=")
        return read_pdf(data, origin, pages)
    image = _image_type(data) or (mime if mime in IMAGE_TYPES.values() else "")
    if image:
        if len(data) > MAX_IMAGE_BYTES:
            raise ReadError(f"{name} is {len(data) // (1024 * 1024)} MB; images up to {MAX_IMAGE_BYTES // (1024 * 1024)} MB are read")
        return Material("image", name, origin, image=data, mime=image, slide_src=slide_src)
    if mime.startswith(("image/", "video/", "audio/")) or b"\0" in data[:4096]:
        raise ReadError(
            f"{name} is {mime or 'a binary file'}: links and files that are web pages, PDFs, PNG, JPEG, GIF or WebP images, or text can be read"
        )
    if pages:
        raise ReadError(f"pages= is for PDFs; read a long {'page' if mime in HTML_TYPES else 'text'} in pieces with part=")
    m = re.search(r"charset=([\w.-]+)", ctype, re.I)
    try:
        text = data.decode(m.group(1) if m else "utf-8", errors="replace")
    except (LookupError, UnicodeError):  # an unknown charset, or a codec such as idna that cannot replace
        text = data.decode("utf-8", errors="replace")
    if mime in HTML_TYPES or (not mime and re.match(r"\s*(<!doctype html|<html)", text[:1000], re.I)):
        title, md = html_to_markdown(text, origin)
        kind = "page"
    else:
        md = text.strip()
        title = next((ln.lstrip("# ").strip() for ln in md.splitlines() if ln.strip()), name)
        kind = "text"
    title = " ".join(title.split())[:MAX_TITLE]
    chunks = close_fences(split_text(md, MAX_CHARS))
    if not 1 <= part <= len(chunks):
        raise ReadError(f"part={part} does not exist: {name} has {len(chunks)} part(s)")
    body = chunks[part - 1]
    return Material(kind, title or name, origin, text=body, part=part, parts=len(chunks), words=len(body.split()))


def close_fences(chunks: list[str]) -> list[str]:
    """A code block cut between two pieces is closed at the end of one and reopened in the next."""
    out, carry = [], ""
    for c in chunks:
        c = carry + c
        carry = ""
        if sum(1 for ln in c.split("\n") if ln.startswith("```")) % 2:
            c += "\n```"
            carry = "```\n"
        out.append(c)
    return out


def _image_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def split_text(text: str, limit: int) -> list[str]:
    """Pieces of at most about `limit` characters, cut between paragraphs where there are any."""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for para in re.split(r"\n{2,}", text):
        while len(para) > limit:  # one paragraph longer than a piece
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + 2 + len(para) > limit:
            out.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


# -- PDF ----------------------------------------------------------------------------------------


def read_pdf(data: bytes, origin: str, pages: str = "") -> Material:
    from pypdf import PdfReader
    from pypdf.errors import DependencyError, PdfReadError

    name = Path(urlparse(origin).path).name or origin
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted and not r.decrypt(""):
            raise ReadError(f"{name} is protected by a password")
        n = len(r.pages)
    except DependencyError:
        raise ReadError(f"{name} is encrypted in a way that needs the cryptography package to open") from None
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        raise ReadError(f"{name} is not a PDF that can be read: {e}") from None
    if n == 0:
        raise ReadError(f"{name} has no pages")
    try:
        a, b = sources.parse_range(pages)
    except sources.SourceError:
        raise ReadError(f"pages={pages} is not a page range like 1-20") from None
    if a == 0 or b == 0:
        raise ReadError(f"pages={pages}: pages are numbered from 1")
    a = a or 1
    b = min(n, b or n)
    if a > n:
        raise ReadError(f"pages={pages} is past the end: {name} has {n} pages")
    out: list[str] = []
    size = 0
    last = a - 1
    empty = True
    for i in range(a - 1, b):
        try:
            t = (r.pages[i].extract_text() or "").strip()
            empty = empty and not t
        except Exception as e:  # pypdf raises many kinds on damaged pages; say so and read on
            t = f"(The text of this page could not be read: {type(e).__name__}.)"
            empty = False
        chunk = f"## Page {i + 1}\n\n{t}" if t else f"## Page {i + 1}\n\n(No text on this page.)"
        if size + len(chunk) > MAX_CHARS:
            if out:
                break
            chunk = chunk[:MAX_CHARS] + "\n\n(The page is cut here: it holds more text than one reply.)"
        out.append(chunk)
        size += len(chunk) + 2
        last = i + 1
    text = "\n\n".join(out)
    title = ""
    try:
        title = str((r.metadata or {}).get("/Title") or "").strip()
    except Exception:  # broken metadata is not worth failing the read for
        pass
    title = " ".join((title or Path(name).stem).split())[:MAX_TITLE]
    return Material(
        "pdf", title, origin, text=text, pages=n, shown=(a, last), asked_end=b, no_text=empty, outline=_outline(r), words=len(text.split())
    )


def _outline(r) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []

    def walk(items, depth: int) -> None:
        for it in items:
            if len(out) >= MAX_OUTLINE:
                return
            if isinstance(it, list):
                walk(it, depth + 1)
                continue
            try:
                page = r.get_destination_page_number(it) + 1
            except Exception:  # an entry pointing nowhere is shown without a page
                page = 0
            title = re.sub(r"\s+", " ", str(getattr(it, "title", "") or "")).strip()
            if title:
                out.append((depth, page, title[:160]))

    try:
        walk(r.outline, 0)
    except Exception:  # a damaged outline only costs the outline
        return out
    return out


# -- HTML ---------------------------------------------------------------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
CLOSES_P = {"p", "div", "ul", "ol", "dl", "table", "pre", "blockquote", "section", "article", "figure", "hr", "form",
            "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "main", "aside", "nav"}  # fmt: skip
SKIP = {"script", "style", "noscript", "template", "svg", "nav", "aside", "form", "iframe", "button", "select",
        "dialog", "canvas", "object", "embed", "input", "textarea", "head"}  # fmt: skip
BOILER_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search", "dialog", "alert"}
BOILER_TOKENS = {"cookie", "cookies", "consent", "newsletter", "subscribe", "share", "sharing", "social", "related",
                 "recommended", "comments", "advert", "advertisement", "ad", "ads", "promo", "sponsored", "sidebar",
                 "breadcrumb", "breadcrumbs", "navbox", "navbar", "menu", "toc", "editsection", "noprint", "banner",
                 "popup", "modal", "skip"}  # fmt: skip
BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "figure", "figcaption", "address", "details",
         "summary", "dl", "dt", "dd", "center", "body", "html", "hr", "caption"}  # fmt: skip
DISPLAYSTYLE = re.compile(r"^\{\\displaystyle\s*(.*)\}$", re.S)  # how MediaWiki wraps a formula
HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


class _Node:
    __slots__ = ("tag", "attrs", "kids", "parent", "depth")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag, self.attrs, self.kids, self.parent = tag, attrs, [], parent
        self.depth = parent.depth + 1 if parent else 0


class _Tree(HTMLParser):
    """A forgiving tree: unclosed <p>, <li>, <td> and <tr> are closed where a browser would."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = self.cur = _Node("#root", {}, None)

    def _close_to(self, tag: str, stop: set[str]) -> None:
        n = self.cur
        while n.parent is not None and n.tag not in stop:
            if n.tag == tag:
                self.cur = n.parent
                return
            n = n.parent

    def handle_starttag(self, tag, attrs):
        if tag in CLOSES_P and self.cur.tag == "p":
            self.cur = self.cur.parent or self.root
        if tag == "li":
            self._close_to("li", {"ul", "ol"})
        elif tag in ("td", "th"):
            self._close_to("td", {"tr", "table"})
            self._close_to("th", {"tr", "table"})
        elif tag == "tr":
            self._close_to("tr", {"table"})
        n = _Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.kids.append(n)
        if tag not in VOID and n.depth < MAX_DEPTH:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.kids.append(_Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.kids.append(data)


def _find(n: _Node, tag: str) -> list[_Node]:
    found, stack = [], [n]
    while stack:
        k = stack.pop()
        if k.tag == tag:
            found.append(k)
        stack.extend(c for c in k.kids if isinstance(c, _Node) and c.tag not in SKIP)
    return found


def _meta(tree: _Node) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _find_all(tree, "meta"):
        key = (m.attrs.get("property") or m.attrs.get("name") or "").lower()
        if key and m.attrs.get("content"):
            out.setdefault(key, m.attrs["content"].strip())
    titles = _find_all(tree, "title")
    if titles:
        out.setdefault("#title", re.sub(r"\s+", " ", "".join(c for c in titles[0].kids if isinstance(c, str))).strip())
    return out


def _find_all(n: _Node, tag: str) -> list[_Node]:
    """Like _find, but also inside the parts skipped as page furniture (meta tags live in <head>)."""
    found, stack = [], [n]
    while stack:
        k = stack.pop()
        if k.tag == tag:
            found.append(k)
        stack.extend(c for c in k.kids if isinstance(c, _Node))
    return found


def _lengths(tree: _Node) -> dict[int, int]:
    """Text length under every node, by id, in one pass from the leaves up."""
    order, stack = [], [tree]
    while stack:
        k = stack.pop()
        order.append(k)
        stack.extend(c for c in k.kids if isinstance(c, _Node) and c.tag not in SKIP)
    out: dict[int, int] = {}
    for k in reversed(order):  # children before their parents
        out[id(k)] = sum(len(c.strip()) if isinstance(c, str) else out.get(id(c), 0) for c in k.kids)
    return out


def _content_root(tree: _Node) -> _Node:
    """The article if the page marks one, else its main part, else the body."""
    length = _lengths(tree)

    def size(n: _Node) -> int:
        return length.get(id(n), 0)

    mains = _find(tree, "main") or [k for k in _find_all(tree, "div") if k.attrs.get("role") == "main"]
    main = max(mains, key=size) if mains else None
    articles = _find(tree, "article")
    if articles:
        best = max(articles, key=size)
        if main is None or size(best) >= 0.4 * size(main):
            return best
    if main is not None:
        return main
    bodies = _find(tree, "body")
    return bodies[0] if bodies else tree


class _Render:
    def __init__(self, base: str, keep_frame: bool) -> None:
        self.base, self.keep_frame = base, keep_frame
        self.blocks: list[str] = []
        self.buf: list[str] = []
        self.images = 0

    def child(self) -> _Render:
        r = _Render(self.base, self.keep_frame)
        r.images = MAX_IMAGES  # images inside lists, quotes and tables are left out
        return r

    def skip(self, n: _Node) -> bool:
        a = n.attrs
        if n.tag in SKIP or "hidden" in a or a.get("aria-hidden") == "true" or a.get("role") in BOILER_ROLES:
            return True
        if n.tag in ("header", "footer") and not self.keep_frame:
            return True
        tokens = set(re.split(r"[\s_-]+", f"{a.get('class', '')} {a.get('id', '')}".lower()))
        return n.tag in ("div", "section", "ul", "ol", "span", "table", "p", "header", "footer") and bool(tokens & BOILER_TOKENS)

    def flush(self) -> None:
        text = "\n".join(re.sub(r"[ \t ]+", " ", ln).strip() for ln in "".join(self.buf).split("\n")).strip()
        self.buf = []
        if text:
            self.blocks.append(re.sub(r"\n{2,}", "\n", text))

    def walk(self, kids) -> None:
        for k in kids:
            if isinstance(k, str):
                self.buf.append(re.sub(r"\s+", " ", k))
                continue
            if self.skip(k):
                continue
            t = k.tag
            if t in HEADINGS:
                self.flush()
                text = _plain(k, self)
                if text:
                    self.blocks.append("#" * HEADINGS[t] + " " + text)
            elif t == "pre":
                self.flush()
                code = _raw(k).strip("\n")
                if code.strip():
                    self.blocks.append(f"```\n{code}\n```")
            elif t in ("ul", "ol"):
                self.flush()
                lines = self.list_lines(k, 0)
                if lines:
                    self.blocks.append("\n".join(lines))
            elif t == "table":
                self.flush()
                self.table(k)
            elif t == "blockquote":
                self.flush()
                sub = self.child()
                sub.walk(k.kids)
                sub.flush()
                if sub.blocks:
                    self.blocks.append("\n".join("> " + ln for ln in "\n\n".join(sub.blocks).split("\n")))
            elif t == "br":
                self.buf.append("\n")
            elif t == "img":
                self.image(k)
            elif t == "math":
                tex = k.attrs.get("alttext", "").strip()
                if tex:
                    self.buf.append(f" ${DISPLAYSTYLE.sub(r'\1', tex)}$ ")
            elif t == "code":
                code = re.sub(r"\s+", " ", _raw(k)).strip()
                if code:
                    self.buf.append(f"`{code}`")
            elif t in BLOCK:
                self.flush()
                self.walk(k.kids)
                self.flush()
            else:
                self.walk(k.kids)

    def list_lines(self, n: _Node, depth: int) -> list[str]:
        lines, i = [], 0
        for li in n.kids:
            if not isinstance(li, _Node) or li.tag != "li" or self.skip(li):
                continue
            i += 1
            sub = self.child()
            nested: list[str] = []
            for c in li.kids:
                if isinstance(c, _Node) and c.tag in ("ul", "ol") and not self.skip(c):
                    nested += self.list_lines(c, depth + 1)
                else:
                    sub.walk([c])
            sub.flush()
            text = " ".join(" ".join(sub.blocks).split())
            mark = f"{i}." if n.tag == "ol" else "-"
            if text:
                lines.append("  " * depth + f"{mark} {text}")
            lines += nested
        return lines

    def table(self, n: _Node) -> None:
        rows: list[tuple[list[str], bool]] = []
        trs: list[_Node] = []
        stack = [n]
        while stack:  # rows of this table in document order, not those of tables inside it
            k = stack.pop()
            if k is not n and k.tag == "tr":
                trs.append(k)
                continue
            stack.extend(c for c in reversed(k.kids) if isinstance(c, _Node) and c.tag != "table")
        for tr in trs:
            cells = [c for c in tr.kids if isinstance(c, _Node) and c.tag in ("td", "th")]
            texts = [_plain(c, self).replace("|", "\\|") for c in cells]
            if any(texts):
                rows.append((texts, all(c.tag == "th" for c in cells)))
        if not rows:
            return
        width = max(len(r) for r, _ in rows)
        out = []
        for i, (r, _) in enumerate(rows[:MAX_TABLE_ROWS]):
            out.append("| " + " | ".join(r + [""] * (width - len(r))) + " |")
            if i == 0:
                out.append("|" + " --- |" * width)
        if len(rows) > MAX_TABLE_ROWS:
            out.append(f"({len(rows) - MAX_TABLE_ROWS} more rows)")
        self.blocks.append("\n".join(out))

    def image(self, n: _Node) -> None:
        a = n.attrs
        src = a.get("src") or a.get("data-src") or ""
        if not src or src.startswith("data:") or "math-fallback" in a.get("class", "") or self.images >= MAX_IMAGES:
            return
        if (a.get("width") or "").isdigit() and int(a["width"]) <= 2:
            return  # a tracking pixel
        url = urljoin(self.base, src)
        if not url.startswith(("http://", "https://")):
            return
        self.flush()
        self.images += 1
        alt = re.sub(r"\s+", " ", a.get("alt", "")).strip().replace("]", "")
        self.blocks.append(f"![{alt}]({url})")


def _plain(n: _Node, r: _Render) -> str:
    sub = r.child()
    sub.walk(n.kids)
    sub.flush()
    return " ".join(" ".join(sub.blocks).split())


def _raw(n: _Node) -> str:
    out, stack = [], [n]
    while stack:
        k = stack.pop()
        if isinstance(k, str):
            out.append(k)
        elif k.tag == "br":
            out.append("\n")
        elif k.tag not in SKIP:
            stack.extend(reversed(k.kids))
    return "".join(out)


def html_to_markdown(html: str, base: str = "") -> tuple[str, str]:
    """(title, Markdown of the page's main text): headings, paragraphs, lists, code, tables,
    quotes, formulas and image links, without menus, banners, scripts or forms."""
    p = _Tree()
    p.feed(html)
    p.close()
    meta = _meta(p.root)
    root = _content_root(p.root)
    r = _Render(base, keep_frame=root.tag == "article")
    r.walk(root.kids)
    r.flush()
    title = meta.get("og:title") or meta.get("twitter:title") or meta.get("#title") or ""
    if not title:
        h1 = _find(root, "h1")
        title = _plain(h1[0], r) if h1 else ""
    byline = [
        x
        for x in (meta.get("author") or meta.get("article:author", ""), (meta.get("article:published_time") or meta.get("date", ""))[:10])
        if x and not x.startswith("http")
    ]
    head = [f"# {title}"] if title else []
    if byline:
        head.append(" · ".join(byline))
    body = r.blocks
    if body and title and body[0].lstrip("# ").strip() == title.strip():
        body = body[1:]
    return title, "\n\n".join(head + body)
