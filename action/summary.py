"""Markdown for a pull request comment and the job summary: the walkthrough's outline with start
times, the download link, and the transcript folded away. Used by action.yml.

    python action/summary.py out/site/data.json > summary.md

`{{ARTIFACT_URL}}` in the output is replaced by the action once the artifact is uploaded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from showntell.export import slide_starts, timeline_total
from showntell.model import Walkthrough


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def summary(data: dict) -> str:
    w = Walkthrough.from_state({"key": "", "report_key": "", **data})
    starts = slide_starts(w)
    total = timeline_total(w)
    out = [
        f"### ▶ {w.title}",
        "",
        f"A narrated walkthrough, {len(w.slides)} slides, {mmss(total)}. "
        "**[Download it]({{ARTIFACT_URL}})**, unzip, and open `walkthrough.html` in a browser: it plays offline. "
        "Voiced on the runner by [showntell](https://github.com/rahimnathwani/showntell).",
        "",
        "| Starts | Slide |",
        "|---|---|",
    ]
    cell = lambda t: t.replace("|", "\\|")  # noqa: E731
    out += [f"| {mmss(starts.get(s.id, 0))} | {cell(s.title)} |" for s in w.slides]
    out += ["", "<details><summary>Transcript</summary>", ""]
    for s in w.slides:
        text = " ".join(x.text for x in s.says if x.text)
        if text:
            out += [f"**{s.title}** ({mmss(starts.get(s.id, 0))})", "", text, ""]
    if w.questions:
        out += ["**Questions to ask next**", ""] + [f"- {q}" for q in w.questions] + [""]
    out += ["</details>", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(summary(json.loads(Path(sys.argv[1]).read_text())))
