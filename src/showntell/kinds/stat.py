"""```stat: one to four headline numbers, each a value or `before -> after`."""

from __future__ import annotations

import re

from .base import Finding, Kind, slug

STAT_MAX = 4


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
        it = {"id": slug(label), "label": label, "after": nums[-1][0], "text": parts[-1],
              "unit": nums[-1][1] or attrs.get("unit", ""), "decimals": nums[-1][2]}
        if len(parts) == 2:
            it["before"] = nums[0][0]
        items.append(it)
    return {"items": items, "ids": [i["id"] for i in items], "title": attrs.get("title", "")}, problems


def _value(text: str) -> tuple[float, str, int] | None:
    # A unit is any short run of non-digits after the number: ms, %, °C, cm², million km.
    m = re.fullmatch(r"([-+]?[\d,_]*\.?\d+)\s*([^\d\s][^\d]{0,15}?|[^\d\s]*[²³])?\s*", text.strip())
    if not m:
        return None
    num = m.group(1).replace(",", "").replace("_", "")
    return float(num), m.group(2) or "", len(num.split(".")[1]) if "." in num else 0


def _check(vis: dict, line: int, limits: dict) -> list[Finding]:
    if len(vis["items"]) <= STAT_MAX:
        return []
    return [("block_too_big", f"The stat block on line {line} has {len(vis['items'])} numbers; a slide holds {STAT_MAX} at a glance.",
             "Keep the ones that matter, or use a chart.")]


KIND = Kind("stat", ("stat",), parse_stat, check=_check, part=lambda vis: "number")
