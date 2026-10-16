"""Bundled slide kinds beyond the core set: charts, terminal sessions and file trees.

Each parser takes a fenced block's body and info-string options and returns the slide's visual,
plus problems to report. Every part a cue can point at has an id, and the parts come in source
order, so cues, step reveals and checks work as they do for lists. The catalogue the agent can
read is text/kinds.json (resource showntell://slide-kinds).
"""

from __future__ import annotations

import re

TERMINAL_LANGS = ("terminal", "console", "shell-session")
CHAT_LANGS = ("chat", "exchange")
ASKERS = {"user", "you", "me", "human", "client", "browser", "request", "dev", "developer"}
PROMPT_RE = re.compile(r"^(\$|%|>|❯|PS [^>]*>)\s")
TREE_RE = re.compile(r"^((?:[│|]\s{1,3}|\s{2,4})*)(?:[├└|`]\s*[─-]{1,3}\s*)?(.*)$")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "item"


def _num(text: str) -> float | None:
    t = text.strip().replace(",", "").replace("_", "")
    t = re.sub(r"(?<=\d)\s*(%|ms|s|kb|mb|gb|x)$", "", t, flags=re.I)
    try:
        return float(t)
    except ValueError:
        return None


# -- charts ---------------------------------------------------------------------------------------


def parse_chart(body: str, attrs: dict) -> tuple[dict, list[str]]:
    """```chart bar|line [title="..."] [unit="ms"] [x="..."] [y="..."]

    bar:  one line per category, `label: value` or `label: v1, v2` with a `series: a, b` line first.
    line: an `x: 1, 2, 4` line first, then one line per series, `name: v1, v2, v3`.
    """
    flags = [f for f in attrs.get("flags", []) if f in ("bar", "line", "hbar")]
    ctype = flags[0] if flags else attrs.get("type", "bar")
    problems: list[str] = []
    rows: list[tuple[str, list[str]]] = []
    notes: list[tuple[str, str]] = []
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        nm = re.match(r"^note\s+(.+?):\s*(.+)$", line, re.I)
        if nm:
            notes.append((nm.group(1).strip(), nm.group(2).strip()))
            continue
        if ":" not in line:
            problems.append(f"chart line `{line[:40]}` has no `label: value`")
            continue
        k, v = line.split(":", 1)
        rows.append((k.strip(), [x.strip() for x in v.split(",") if x.strip()]))
    vis: dict = {"type": ctype, "title": attrs.get("title", ""), "unit": attrs.get("unit", ""),
                 "xlabel": attrs.get("x", ""), "ylabel": attrs.get("y", "")}
    if ctype == "line":
        if not rows or rows[0][0].lower() != "x":
            problems.append("a line chart starts with an `x: ...` line giving the x values")
            vis.update(x=[], series=[], ids=[])
            return vis, problems
        xs = rows[0][1]
        series = []
        for name, vals in rows[1:]:
            nums = [_num(v) for v in vals]
            if any(n is None for n in nums):
                problems.append(f"series `{name}` has a value that is not a number")
            if len(nums) != len(xs):
                problems.append(f"series `{name}` has {len(nums)} values for {len(xs)} x values")
            series.append({"id": _slug(name), "name": name, "values": [n if n is not None else 0 for n in nums][: len(xs)]})
        vis.update(x=xs, series=series, ids=[s["id"] for s in series])
        vis["notes"] = _notes(notes, vis, problems)
        return vis, problems
    names: list[str] = []
    if rows and rows[0][0].lower() == "series":
        names = rows.pop(0)[1]
    cats = []
    for label, vals in rows:
        nums = [_num(v) for v in vals]
        if not nums or any(n is None for n in nums):
            problems.append(f"bar `{label}` needs numbers after the colon")
        cats.append({"id": _slug(label), "label": label, "values": [n if n is not None else 0 for n in nums]})
    width = max((len(c["values"]) for c in cats), default=1)
    names = names[:width] + [f"series {i + 1}" for i in range(len(names), width)] if width > 1 else names[:1]
    vis.update(categories=cats, series=[{"id": _slug(n), "name": n} for n in names], ids=[c["id"] for c in cats])
    vis["notes"] = _notes(notes, vis, problems)
    return vis, problems


def _notes(raw: list[tuple[str, str]], vis: dict, problems: list[str]) -> list[dict]:
    """`note 800: text` (an x value or bar label), or `note p99/800: text` for one series."""
    out = []
    for where, text in raw:
        series, _, at = where.rpartition("/")
        if vis["type"] == "line":
            xs = vis.get("x", [])
            if at not in xs:
                problems.append(f"note `{where}`: no x value `{at}`; x values are {', '.join(xs)}")
                continue
            sid = _slug(series) if series else None
            if sid and sid not in vis["ids"]:
                problems.append(f"note `{where}`: no series `{series}`")
                continue
            out.append({"id": f"note-{len(out) + 1}", "x": xs.index(at), "series": sid, "text": text})
        else:
            ids = [c["id"] for c in vis.get("categories", [])]
            if _slug(at) not in ids:
                problems.append(f"note `{where}`: no bar `{at}`")
                continue
            out.append({"id": f"note-{len(out) + 1}", "bar": _slug(at), "text": text})
    return out


# -- big numbers -----------------------------------------------------------------------------------


def parse_stat(body: str, attrs: dict) -> tuple[dict, list[str]]:
    """```stat [unit="ms"]: one `label: value` or `label: before -> after` line per number. Values
    may carry their own unit (`2.1%`, `420 ms`)."""
    items, problems = [], []
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            problems.append(f"stat line `{line[:40]}` has no `label: value`")
            continue
        label, value = (x.strip() for x in line.split(":", 1))
        parts = [x.strip() for x in re.split(r"\s*(?:->|→)\s*", value)]
        nums = [_value(x) for x in parts]
        if not 1 <= len(parts) <= 2 or any(n is None for n in nums):
            problems.append(f"stat `{label}` needs a number, or `before -> after`")
            continue
        it = {"id": _slug(label), "label": label, "after": nums[-1][0], "text": parts[-1],
              "unit": nums[-1][1] or attrs.get("unit", ""), "decimals": nums[-1][2]}
        if len(parts) == 2:
            it["before"] = nums[0][0]
        items.append(it)
    return {"items": items, "ids": [i["id"] for i in items], "title": attrs.get("title", "")}, problems


def _value(text: str) -> tuple[float, str, int] | None:
    m = re.fullmatch(r"([-+]?[\d,_]*\.?\d+)\s*([a-zA-Z%µ/]+)?", text.strip())
    if not m:
        return None
    num = m.group(1).replace(",", "").replace("_", "")
    return float(num), m.group(2) or "", len(num.split(".")[1]) if "." in num else 0


# -- exchanges -------------------------------------------------------------------------------------


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


# -- terminal sessions -----------------------------------------------------------------------------


def parse_terminal(body: str, attrs: dict) -> dict:
    """```terminal: lines starting with a prompt ($, %, >) are commands; the rest is output."""
    lines = body.rstrip("\n").split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = PROMPT_RE.match(ln)
        out.append({"n": i + 1, "cmd": bool(m), "prompt": m.group(1) if m else "", "text": ln[m.end():] if m else ln})
    start = int(attrs["start"]) if str(attrs.get("start", "")).isdigit() else 1
    return {"code": "\n".join(lines), "start": start, "lines": out, "title": attrs.get("title", "Terminal")}


# -- file trees ------------------------------------------------------------------------------------


def parse_tree(body: str, attrs: dict) -> tuple[dict, list[str]]:
    """```tree: one entry per line, nested by indentation (two or four spaces) or by `tree` output
    (├── └── │). A trailing / or deeper lines below mark a folder; `# text` after an entry is a note."""
    rows, problems = [], []
    unit = None
    for raw in body.split("\n"):
        if not raw.strip():
            continue
        raw = raw.rstrip()
        m = TREE_RE.match(raw)
        lead, rest = m.group(1), m.group(2).strip()
        if re.match(r"^[\s│|]*[├└`]", raw):  # drawn by `tree`
            depth = len(re.findall(r"[│|]\s{1,3}|\s{4}", lead)) + 1
        else:
            spaces = len(raw) - len(raw.lstrip(" "))
            if spaces and unit is None:
                unit = spaces
            depth = spaces // (unit or 2)
        note = ""
        if " #" in rest:
            rest, _, note = rest.partition(" #")
            rest, note = rest.strip(), note.strip()
        if rest and rest != ".":
            rows.append([depth, rest, note])
    if rows:
        base = min(r[0] for r in rows)
        for r in rows:
            r[0] -= base
    items, stack = [], []
    for i, (depth, name, note) in enumerate(rows):
        folder = name.endswith("/") or (i + 1 < len(rows) and rows[i + 1][0] > depth)
        if depth > len(stack):
            problems.append(f"`{name}` is indented deeper than the entry above it")
            depth = len(stack)
        del stack[depth:]
        clean = name.rstrip("/")
        items.append({"id": "/".join([*stack, clean]), "name": clean + ("/" if folder else ""), "depth": depth,
                      "folder": folder, "note": note})
        if folder:
            stack.append(clean)
    seen: set[str] = set()
    for it in items:
        while it["id"] in seen:
            it["id"] += "~"
        seen.add(it["id"])
    return {"items": items, "ids": [it["id"] for it in items], "title": attrs.get("title", "")}, problems


def find_part(vis: dict, target: str) -> str | None:
    """Id of the chart bar, series or tree entry a cue names: its id, label, path or file name."""
    t = target.strip().lstrip("/")
    ids = vis.get("ids", [])
    if t in ids or t in [n["id"] for n in vis.get("notes", [])]:
        return t
    low = t.lower()
    hits = []
    for part in vis.get("categories", []) + vis.get("series", []) + vis.get("items", []):
        names = {part.get("label", ""), part.get("name", ""), part.get("name", "").rstrip("/")}
        if (low in {n.lower() for n in names if n} or _slug(t) == part.get("id")) and part.get("id") in ids:
            hits.append(part["id"])
    if len(set(hits)) == 1:
        return hits[0]
    tails = [i for i in ids if i.endswith("/" + t)]
    return tails[0] if len(tails) == 1 else None


def labels(vis: dict) -> list[tuple[str, str]]:
    """(id, spoken name) for every part a cue can point at, used to infer cues from narration."""
    out = []
    for part in vis.get("categories", []) + vis.get("series", []) + vis.get("items", []):
        if part.get("id") not in vis.get("ids", []):
            continue
        name = part.get("label") or part.get("name", "").rstrip("/")
        out.append((part["id"], name))
    return out
