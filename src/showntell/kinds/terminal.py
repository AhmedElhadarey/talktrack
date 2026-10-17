"""```terminal: a shell session, commands and their output, cued by line like code."""

from __future__ import annotations

import re

from .base import Kind

TERMINAL_LANGS = ("terminal", "console", "shell-session")
PROMPT_RE = re.compile(r"^(\$|%|>|❯|PS [^>]*>)\s")


def parse_terminal(body: str, attrs: dict) -> dict:
    """```terminal: lines starting with a prompt ($, %, >) are commands; the rest is output."""
    lines = body.rstrip("\n").split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = PROMPT_RE.match(ln)
        out.append({"n": i + 1, "cmd": bool(m), "prompt": m.group(1) if m else "", "text": ln[m.end():] if m else ln})
    start = int(attrs["start"]) if str(attrs.get("start", "")).isdigit() else 1
    return {"code": "\n".join(lines), "start": start, "lines": out, "title": attrs.get("title", "Terminal")}


KIND = Kind("terminal", TERMINAL_LANGS, lambda body, attrs: (parse_terminal(body, attrs), []), lines=True)
