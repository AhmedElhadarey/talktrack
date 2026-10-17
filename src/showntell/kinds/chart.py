"""```chart bar|hbar|line: numbers as bars or lines, with optional callouts."""

from __future__ import annotations

import re

from .base import Finding, Kind, slug

CHART_MAX = 12


def _num(text: str) -> float | None:
    t = text.strip().replace(",", "").replace("_", "")
    t = re.sub(r"(?<=\d)\s*(%|ms|s|kb|mb|gb|x)$", "", t, flags=re.I)
    try:
        return float(t)
    except ValueError:
        return None


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
            series.append({"id": slug(name), "name": name, "values": [n if n is not None else 0 for n in nums][: len(xs)]})
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
        cats.append({"id": slug(label), "label": label, "values": [n if n is not None else 0 for n in nums]})
    width = max((len(c["values"]) for c in cats), default=1)
    names = names[:width] + [f"series {i + 1}" for i in range(len(names), width)] if width > 1 else names[:1]
    vis.update(categories=cats, series=[{"id": slug(n), "name": n} for n in names], ids=[c["id"] for c in cats])
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
            sid = slug(series) if series else None
            if sid and sid not in vis["ids"]:
                problems.append(f"note `{where}`: no series `{series}`")
                continue
            out.append({"id": f"note-{len(out) + 1}", "x": xs.index(at), "series": sid, "text": text})
        else:
            ids = [c["id"] for c in vis.get("categories", [])]
            if slug(at) not in ids:
                problems.append(f"note `{where}`: no bar `{at}`")
                continue
            out.append({"id": f"note-{len(out) + 1}", "bar": slug(at), "text": text})
    return out


def _part(vis: dict) -> str:
    return "series" if vis.get("type") == "line" else "bar"


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    n = len(vis["ids"])
    if n <= CHART_MAX:
        return []
    return [("block_too_big", f"The chart on line {line} has {n} {'series' if vis['type'] == 'line' else 'bars'}; "
             f"charts read well up to {CHART_MAX}.", "Group the small ones, or split the chart.")]


KIND = Kind("chart", ("chart",), parse_chart, check=_check, part=_part)
