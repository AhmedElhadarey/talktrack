"""```tree: where files live, nested by indentation or pasted from `tree`."""

from __future__ import annotations

import re

from .base import Finding, Kind

TREE_RE = re.compile(r"^((?:[│|]\s{1,3}|\s{2,4})*)(?:[├└|`]\s*[─-]{1,3}\s*)?(.*)$")


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


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    if len(vis["items"]) <= limits["lines"]:
        return []
    return [("code_too_tall", f"The file tree on line {line} has {len(vis['items'])} entries; this frame reads well up to {limits['lines']}.",
             "Show only the folders that matter, with `...` for the rest.")]


KIND = Kind("tree", ("tree",), parse_tree, check=_check, part=lambda vis: "entry", error_code="tree_error",
            hint="Indent each level by the same amount.")
