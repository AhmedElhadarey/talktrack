# /// script
# requires-python = ">=3.12"
# ///
"""Clean-room check: list 7-word runs shared between this repository's text and a folder of
reference material kept outside it. Run before releases:

    uv run scripts/provenance_check.py ~/path/to/reference

Shared HTML boilerplate (doctype, charset) is expected; anything else should be reworded.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

N = 7
REF_TYPES = {".md", ".txt", ".json", ".js", ".ts", ".html", ".xml", ".py", ""}


def shingles(text: str) -> set[str]:
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {" ".join(w[i : i + N]) for i in range(len(w) - N + 1)}


def main() -> int:
    ref_root = Path(sys.argv[1]).expanduser()
    repo = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1]
    ref: set[str] = set()
    for f in ref_root.rglob("*"):
        if f.is_file() and f.suffix in REF_TYPES and f.stat().st_size < 5_000_000:
            ref |= shingles(f.read_text(errors="ignore"))
    ours = [
        *repo.glob("src/talktrack/text/*"),
        *repo.glob("docs/*.md"),
        repo / "README.md",
        repo / "GEMINI.md",
        *repo.glob("claude-plugin/skills/*/SKILL.md"),
        *repo.glob("src/talktrack/static/**/*.js"),
        *repo.glob("src/talktrack/static/*.html"),
        *repo.glob("src/talktrack/static/*.css"),
    ]
    ours = [f for f in ours if f.is_file() and "vendor" not in f.parts]
    hits = 0
    for f in ours:
        common = shingles(f.read_text(errors="ignore")) & ref
        if common:
            hits += 1
            print(f.relative_to(repo), sorted(common)[:8])
    print(
        f"{len(ours)} files checked against {len(ref)} reference runs: " + ("clean" if not hits else f"{hits} file(s) share {N}-word runs")
    )
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
