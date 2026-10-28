"""Incremental parsing: open fences wait for the next chunk, and splitting at blank lines changes nothing."""

from __future__ import annotations

import random

import pytest

from talktrack.format import Parser

DOC = """---
title: Cache walkthrough
frame: square
---

Intro before any heading.

# How the cache lookup works {#intro}
> A read-through cache

Every request checks the cache first.

## The lookup {#lookup .build}

```python file=src/cache.py lines=10-24
```

The method starts by [checking](#L12 "if key").
On a miss it [calls the loader](#L18).

## Inline code

```python start=5
def get(self, key):

    return self._data[key]
```

{pause=1.5}

## Storage {.cards}

- **Memory**: fast {#memory}
- **Disk**: survives restarts {#disk}

1. first
2. second

| Name | Speed |
|:--|--:|
| `mem` | fast |

$$
e = mc^2

f = ma
$$

$$ a^2 + b^2 = c^2 $$

```math
\\frac{a}{b}
```

![Timeline](docs/timeline.png)

Fact with a note[^1].

[^1]: The source.

<!-- a comment -->

---

~~~mermaid
flowchart LR
  a[A] --> b[B]

  b --> c[C]
~~~

Closing words.
"""


def snapshot(blocks) -> list[tuple]:
    return [(b.kind, b.line, b.text, b.level, b.attrs, b.items, b.lang) for b in blocks]


def feed_all(chunks: list[str]) -> list[tuple]:
    p = Parser()
    out = []
    for c in chunks:
        out += p.feed(c)
    assert not p.pending.strip()
    return snapshot(out)


def test_open_fence_is_held_until_closed():
    p = Parser()
    first = p.feed("# Code\n\n```python\ndef f():\n")
    assert [b.kind for b in first] == ["heading"]
    assert p.pending.startswith("```python")
    (code, para) = p.feed("    return 1\n```\n\nSpoken.\n")
    assert (code.kind, code.text, code.line) == ("code", "def f():\n    return 1", 3)
    assert (para.kind, para.line) == ("para", 8)
    assert p.pending == ""


def test_fence_split_at_a_blank_line_inside_it():
    p = Parser()
    assert p.feed("```\na\n\n") == []
    (b,) = p.feed("b\n```\n")
    assert b.text == "a\n\nb"


def test_end_of_chunk_closes_paragraphs_and_lists():
    p = Parser()
    assert [b.kind for b in p.feed("Spoken words\n- item")] == ["para"]
    assert [b.kind for b in p.feed("more")] == ["para"]


def test_line_numbers_continue_across_chunks():
    p = Parser()
    p.feed("# A\n\nOne.\n")
    (h, para) = p.feed("\n## B\n\nTwo.\n")
    assert (h.line, para.line) == (5, 7)


def test_whole_document_parses_to_expected_kinds():
    assert [b[0] for b in feed_all([DOC])] == [
        "front",
        "para",
        "heading",
        "quote",
        "para",
        "heading",
        "code",
        "para",
        "heading",
        "code",
        "pause",
        "heading",
        "list",
        "list",
        "table",
        "math",
        "math",
        "math",
        "image",
        "para",
        "footnote",
        "code",
        "para",
    ]


def _split_at_blank_lines(rng: random.Random) -> list[str]:
    lines = DOC.split("\n")
    blanks = [i for i, line in enumerate(lines) if not line.strip()]
    cuts = sorted(rng.sample(blanks, rng.randint(1, len(blanks))))
    chunks, prev = [], 0
    for c in cuts:
        end = c + 1 if rng.random() < 0.5 else c  # chunk ends after the blank line, or the next one starts with it
        if end > prev:
            chunks.append("\n".join(lines[prev:end]) + "\n")
            prev = end
    chunks.append("\n".join(lines[prev:]))
    return chunks


@pytest.mark.parametrize("seed", range(40))
def test_splitting_at_blank_lines_gives_the_same_blocks(seed):
    chunks = _split_at_blank_lines(random.Random(seed))
    assert len(chunks) > 1
    assert feed_all(chunks) == feed_all([DOC])


def test_every_single_blank_line_split_gives_the_same_blocks():
    whole = feed_all([DOC])
    lines = DOC.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            chunks = ["\n".join(lines[: i + 1]) + "\n", "\n".join(lines[i + 1 :])]
            assert feed_all(chunks) == whole, f"split after line {i + 1}"
