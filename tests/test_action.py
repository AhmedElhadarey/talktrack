"""The GitHub Action's own example walkthrough, which CI voices: its cues must still land on the
lines of action.yml they name, and the summary it posts must read the exported data."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_example_cues_match_action_yml(build):
    w, b = build((ROOT / "action" / "example.md").read_text(), root=ROOT)
    b.check_all()
    codes = {i.code for i in b.issues}
    assert not codes & {"cue_moved", "cue_text_missing", "cue_off_code", "source_error"}, b.issues
    assert len(w.slides) == 3


def test_summary_lists_slides_and_transcript(build):
    spec = importlib.util.spec_from_file_location("summary", ROOT / "action" / "summary.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    w, _ = build("# Title | pipes\n\nOpening line.\n\n## Next\n\nSecond line.\n")
    for x in (x for s in w.slides for x in s.says):
        x.duration = 2.0
    md = mod.summary(w.public())  # what data.json holds
    assert "{{ARTIFACT_URL}}" in md and "| Title \\| pipes |" in md and "Second line." in md
