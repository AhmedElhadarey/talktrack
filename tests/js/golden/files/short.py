"""Incremental, lenient parser for the talktrack Markdown format (docs/format.md).

`Parser.feed(chunk)` returns the blocks completed by that chunk. The end of a chunk closes
every open block except a fenced block, a `$$` formula or front matter, which wait for
the next chunk. Inline narration markup is handled by `parse_narration`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
