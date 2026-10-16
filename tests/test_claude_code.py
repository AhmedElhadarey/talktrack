"""Through the real Claude Code: what the model actually receives from showntell's tools.

Claude Code decides which parts of a tool result the model sees (since 2.1.x, only the structured
content when there is any), and nothing else in the suite runs Claude Code itself. Opt in:

    SHOWNTELL_TEST_CLAUDE=1 uv run pytest -m claude

It needs the `claude` CLI, logged in, and costs a few cents per run (Sonnet).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [
    pytest.mark.claude,
    pytest.mark.skipif(not os.environ.get("SHOWNTELL_TEST_CLAUDE"), reason="set SHOWNTELL_TEST_CLAUDE=1 to run"),
    pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI"),
]

PROMPT = """Use the showntell tools, then answer.
1. Call start_walkthrough with title "Probe" and opening "# Probe\\n\\nOne short sentence."
2. Call add_slides with this Markdown for the walkthrough:
## Shape

```mermaid
flowchart LR
  a[Start] --> b[End]
```

It goes from start to end.
3. Call preview_slides for the walkthrough.
Then reply with only a JSON object, no other text:
{"guide": true if the start_walkthrough result contained a section titled "Writing a showntell walkthrough",
 "share": true if that result told you to send the link to the user,
 "link": the walkthrough link you were given,
 "image": true if preview_slides gave you an image you could see,
 "seen": five words describing the second slide's picture, or ""}"""


def test_the_model_receives_the_guide_the_link_and_the_pictures(tmp_path, free_port):
    cfg = {"mcpServers": {"showntell": {
        "command": "uv", "args": ["run", "--quiet", "--directory", str(ROOT), "showntell", "mcp"],
        "env": {"SHOWNTELL_DATA": str(tmp_path / "data"), "SHOWNTELL_PORT": str(free_port), "SHOWNTELL_SPEECH": "fake"},
    }}}
    (tmp_path / "mcp.json").write_text(json.dumps(cfg))
    try:
        r = subprocess.run(
            ["claude", "-p", PROMPT, "--model", "sonnet", "--mcp-config", str(tmp_path / "mcp.json"), "--strict-mcp-config",
             "--allowedTools", "mcp__showntell"],
            cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        m = re.search(r"\{.*\}", r.stdout, re.S)
        assert m, f"no JSON in the answer:\n{r.stdout}\n{r.stderr}"
        got = json.loads(m.group(0))
        assert got["guide"] is True, got
        assert got["share"] is True, got
        assert re.match(r"http://localhost:\d+/w/[\w-]+", got["link"]), got
        assert got["image"] is True and got["seen"], got
    finally:
        lock = tmp_path / "data" / "daemon.json"
        if lock.is_file():  # stop the daemon this test started
            d = json.loads(lock.read_text())
            subprocess.run([sys.executable, "-c", f"import os,signal; os.kill({d['pid']}, signal.SIGTERM)"], check=False)
