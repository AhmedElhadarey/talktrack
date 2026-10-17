"""```chat: an exchange of messages, such as a prompt and its answer or a request and its response."""

from __future__ import annotations

import re

from .base import Finding, Kind

CHAT_LANGS = ("chat", "exchange")
CHAT_MAX = 8
ASKERS = {"user", "you", "me", "human", "client", "browser", "request", "dev", "developer"}


def parse_chat(body: str, attrs: dict) -> tuple[dict, list[str]]:
    """```chat: one message per `name: text` line; lines without a name continue the message
    above. The first name speaks on the left, the second on the right."""
    items, sides, problems = [], {}, []
    for raw in body.split("\n"):
        if not raw.strip():
            if items:
                items[-1]["text"] += "\n"
            continue
        m = re.match(r"^([\w .@-]{1,24}):\s?(.*)$", raw)
        if m and not raw.startswith((" ", "\t")):
            role = m.group(1).strip()
            if role not in sides:
                sides[role] = "left" if not sides else "right" if len(sides) == 1 else "left"
            items.append({"id": f"m{len(items) + 1}", "role": role, "side": sides[role], "text": m.group(2)})
        elif items:
            items[-1]["text"] += ("\n" if not items[-1]["text"].endswith("\n") else "") + raw.strip()
        else:
            problems.append(f"chat line `{raw.strip()[:40]}` comes before any `name:`")
    # The person asking sits on the right, as in chat apps; otherwise the second voice does.
    asker = next((r for r in sides if r.lower() in ASKERS), None)
    for it in items:
        it["text"] = it["text"].strip()
        if asker:
            it["side"] = "right" if it["role"] == asker else "left"
    return {"items": items, "ids": [i["id"] for i in items], "title": attrs.get("title", "")}, problems


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    if len(vis["items"]) <= CHAT_MAX:
        return []
    return [("block_too_big", f"The exchange on line {line} has {len(vis['items'])} messages; a slide holds about {CHAT_MAX}.",
             "Split it across slides, or trim the messages.")]


# Messages have no names to match in narration, so cues are never inferred for them.
KIND = Kind("chat", CHAT_LANGS, parse_chat, check=_check, part=lambda vis: "message",
            hint="Start each message with `name:`.", infer=False)
