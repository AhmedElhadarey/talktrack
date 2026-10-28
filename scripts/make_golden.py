"""Rebuild the golden player-state inputs from their Markdown, voiced by the fake narrator.

    uv run python scripts/make_golden.py                # every tests/js/golden/*.md
    UPDATE_GOLDEN=1 node --test tests/js/              # then rewrite the expected states

Review both diffs: an input changes when the builder changes, and the state when the timeline does.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from talktrack.speech.fake import FakeBackend  # noqa: E402
from talktrack.speech.narrator import Narrator  # noqa: E402
from talktrack.store import Store  # noqa: E402

GOLDEN = ROOT / "tests" / "js" / "golden"


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    narrator = Narrator(FakeBackend(), tmp / "audio")
    narrator.start()
    narrator.wait_ready(5)
    store = Store(tmp / "data", "http://golden", narrator)
    store.loop = asyncio.get_running_loop()
    for md in sorted(GOLDEN.glob("*.md")):
        s = store.create(md.stem, root=str(ROOT))
        s.add(md.read_text())
        for _ in range(400):
            if all(x.status == "ready" for sl in s.w.slides for x in sl.says if x.text):
                break
            await asyncio.sleep(0.02)
        s.finish(None)
        data = s.w.public()
        data["id"] = md.stem
        (GOLDEN / f"{md.stem}.input.json").write_text(json.dumps(data, indent=1) + "\n")
        print("wrote", md.stem)


if __name__ == "__main__":
    asyncio.run(main())
