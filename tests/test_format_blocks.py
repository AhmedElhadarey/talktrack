"""Block parsing for every construct in docs/format.md."""

from __future__ import annotations

import pytest

from showntell.format import Parser, parse_attrs, parse_info, parse_item, parse_table


def parse(text: str):
    return Parser().feed(text)


def kinds(text: str) -> list[str]:
    return [b.kind for b in parse(text)]


# -- front matter ------------------------------------------------------------------------------


def test_front_matter_keys_are_read():
    (b,) = parse("---\ntitle: Cache walk\nframe: square\nvoice: am_adam\nspeed: 1.2\n---\n")
    assert b.kind == "front"
    assert b.attrs == {"title": "Cache walk", "frame": "square", "voice": "am_adam", "speed": "1.2"}


def test_front_matter_quoted_value_keeps_colons():
    (b,) = parse('---\ntitle: "Caching: a tour"\n---\n')
    assert b.attrs["title"] == "Caching: a tour"


def test_front_matter_may_follow_leading_blank_lines():
    assert kinds("\n\n---\ntitle: X\n---\n# H\n") == ["front", "heading"]


def test_dashes_after_content_are_a_rule_not_front_matter():
    assert kinds("Hello.\n\n---\ntitle: X\n---\n") == ["para", "para"]


def test_unclosed_front_matter_waits_for_the_next_chunk():
    p = Parser()
    assert p.feed("---\ntitle: X\n") == []
    (b,) = p.feed("---\n")
    assert b.kind == "front" and b.attrs["title"] == "X"


# -- headings -----------------------------------------------------------------------------------


def test_heading_level_and_text():
    (b,) = parse("### The lookup\n")
    assert (b.kind, b.level, b.text) == ("heading", 3, "The lookup")


def test_heading_attributes_id_classes_and_values():
    (b,) = parse('## The lookup {#lookup .build .cards alt="Request flow" speed=1.1}\n')
    assert b.text == "The lookup"
    assert b.attrs == {"id": "lookup", "classes": ["build", "cards"], "alt": "Request flow", "speed": "1.1"}


def test_heading_closing_hashes_are_dropped():
    (b,) = parse("## Title ##\n")
    assert b.text == "Title"


def test_heading_needs_a_space_after_hashes():
    assert kinds("#hashtag here\n") == ["para"]


def test_unknown_heading_attributes_are_kept_not_fatal():
    (b,) = parse("## T {.zoom data=x}\n")
    assert b.attrs["classes"] == ["zoom"] and b.attrs["data"] == "x"


# -- quotes, paragraphs, rules ------------------------------------------------------------------


def test_quote_lines_join_into_one_block():
    (b,) = parse("> line one\n> line two\n")
    assert (b.kind, b.text) == ("quote", "line one\nline two")


def test_paragraph_lines_join_with_spaces():
    (b,) = parse("first line\n  second line\n")
    assert (b.kind, b.text) == ("para", "first line second line")


def test_blank_line_separates_paragraphs():
    assert kinds("one\n\ntwo\n") == ["para", "para"]


def test_quote_then_paragraph_without_blank_line():
    assert kinds("> shown\nspoken\n") == ["quote", "para"]


def test_horizontal_rule_is_ignored():
    assert kinds("a\n\n---\n\nb\n") == ["para", "para"]


# -- lists ---------------------------------------------------------------------------------------


def test_bullet_list_items():
    (b,) = parse("- **Memory**: fast {#memory}\n- **Disk**: survives restarts {#disk}\n")
    assert b.kind == "list" and b.attrs["ordered"] is False
    assert b.items == ["**Memory**: fast {#memory}", "**Disk**: survives restarts {#disk}"]


def test_numbered_list_is_ordered():
    (b,) = parse("1. first\n2. second\n")
    assert b.attrs["ordered"] is True and b.items == ["first", "second"]


def test_nested_list_is_flattened():
    (b,) = parse("- a\n  - b\n- c\n")
    assert b.items == ["a", "b", "c"]


def test_indented_line_continues_the_last_item():
    (b,) = parse("- a\n  more of a\n- b\n")
    assert b.items == ["a more of a", "b"]


def test_parse_item_title_colon_description_and_id():
    assert parse_item("**Memory**: fast, gone on restart {#memory}") == {
        "id": "memory", "title": "Memory", "body": "fast, gone on restart"}


def test_parse_item_title_with_dash_separator():
    assert parse_item("**Disk** - survives restarts") == {"id": "", "title": "Disk", "body": "survives restarts"}


def test_parse_item_plain_text():
    assert parse_item("just text") == {"id": "", "title": "", "body": "just text"}


# -- tables --------------------------------------------------------------------------------------


TABLE = "| Name | Speed | Notes |\n|:--|:--:|--:|\n| `mem` | **fast** | a \\| b |\n| disk | slow |\n"


def test_table_block():
    (b,) = parse(TABLE)
    assert b.kind == "table" and b.text.count("\n") == 3


def test_parse_table_head_rows_and_alignment():
    t = parse_table(parse(TABLE)[0].text)
    assert t["head"] == ["Name", "Speed", "Notes"]
    assert t["align"] == ["left", "center", "right"]
    assert t["rows"][0] == ["`mem`", "**fast**", "a | b"]


def test_parse_table_pads_short_rows():
    t = parse_table(parse(TABLE)[0].text)
    assert t["rows"][1] == ["disk", "slow", ""]


def test_table_without_outer_pipes():
    assert kinds("a | b\n--- | ---\n1 | 2\n") == ["table"]


def test_table_delimiter_cells_with_one_hyphen():
    assert kinds("| a | b | c |\n|:-|:-:|-|\n| 1 | 2 | 3 |\n") == ["table"]


def test_table_ends_at_blank_line():
    assert kinds("| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter.\n") == ["table", "para"]


# -- fences ----------------------------------------------------------------------------------------


def test_fence_language_and_body():
    (b,) = parse("```python\ndef get(self, key):\n    ...\n```\n")
    assert (b.kind, b.lang, b.text) == ("code", "python", "def get(self, key):\n    ...")


def test_fence_info_options():
    (b,) = parse('```python file=src/cache.py lines=10-24 title="The cache"\n```\n')
    assert b.lang == "python" and b.text == ""
    assert b.attrs["file"] == "src/cache.py" and b.attrs["lines"] == "10-24" and b.attrs["title"] == "The cache"
    assert b.attrs["flags"] == []


def test_fence_info_flags_are_lowercased():
    (b,) = parse("```python OLD\nx\n```\n")
    assert b.attrs["flags"] == ["old"]


def test_fence_info_start_option():
    (b,) = parse("```js start=10\nx\n```\n")
    assert b.attrs["start"] == "10"


def test_fence_info_without_language():
    lang, attrs = parse_info("file=a.py rev=HEAD~1")
    assert lang == "" and attrs["file"] == "a.py" and attrs["rev"] == "HEAD~1"


def test_tilde_fence():
    (b,) = parse("~~~\ncode\n~~~\n")
    assert (b.kind, b.text) == ("code", "code")


def test_longer_fence_holds_shorter_fences():
    (b,) = parse("````markdown\n```python\nx\n```\n````\n")
    assert b.text == "```python\nx\n```"


def test_fence_body_keeps_blank_lines():
    (b,) = parse("```\na\n\nb\n```\n")
    assert b.text == "a\n\nb"


def test_math_fence_is_math():
    (b,) = parse("```math\n\\frac{a}{b}\n```\n")
    assert (b.kind, b.text) == ("math", "\\frac{a}{b}")


def test_mermaid_fence_is_code_with_mermaid_language():
    (b,) = parse("```mermaid\nflowchart LR\n  a --> b\n```\n")
    assert (b.kind, b.lang) == ("code", "mermaid")


def test_html_comment_inside_fence_is_kept():
    (b,) = parse("```html\n<!-- nav -->\n<nav></nav>\n```\n")
    assert b.text == "<!-- nav -->\n<nav></nav>"


# -- $$ math -----------------------------------------------------------------------------------------


def test_dollar_math_on_one_line():
    (b,) = parse("$$ e = mc^2 $$\n")
    assert (b.kind, b.text) == ("math", "e = mc^2")


def test_dollar_math_over_lines():
    (b,) = parse("$$\na + b\n= c\n$$\n")
    assert (b.kind, b.text) == ("math", "a + b\n= c")


def test_dollar_math_waits_for_its_close():
    p = Parser()
    assert p.feed("$$\na\n\n") == []
    (b,) = p.feed("b\n$$\n")
    assert b.kind == "math" and b.text == "a\n\nb"


# -- images, footnotes, pauses, comments -----------------------------------------------------------


def test_image_alt_and_src():
    (b,) = parse("![Request timeline](docs/timeline.png)\n")
    assert b.kind == "image" and b.attrs["alt"] == "Request timeline" and b.attrs["src"] == "docs/timeline.png"


def test_image_with_title_and_attributes():
    (b,) = parse('![Plot](https://example.com/p.png "a title"){width=50}\n')
    assert b.attrs["src"] == "https://example.com/p.png" and b.attrs["width"] == "50"


def test_footnote_definition():
    (b,) = parse("[^src]: The original paper.\n")
    assert (b.kind, b.text, b.attrs["id"]) == ("footnote", "The original paper.", "src")


@pytest.mark.parametrize("line,seconds", [("{pause=1.5}", 1.5), ("{ pause = 2 }", 2.0), ("{pause=0.5s}", 0.5)])
def test_pause_paragraph(line, seconds):
    (b,) = parse(line + "\n")
    assert b.kind == "pause" and b.attrs["seconds"] == seconds


def test_pause_inside_a_paragraph_is_text():
    (b,) = parse("Wait\n{pause=1}\n")
    assert b.kind == "para"


def test_comment_is_dropped():
    assert kinds("<!-- note to self -->\nSpoken.\n") == ["para"]
    assert parse("Spoken <!-- aside --> words.\n")[0].text == "Spoken  words."


def test_multiline_comment_keeps_line_numbers():
    blocks = parse("<!--\na\nb\n-->\n\nAfter.\n")
    assert [(b.kind, b.line) for b in blocks] == [("para", 6)]


# -- attributes and line numbers -------------------------------------------------------------------


def test_parse_attrs_mixed():
    assert parse_attrs("#x .a key='v w' bare") == {"id": "x", "classes": ["a"], "key": "v w", "flags": ["bare"]}


def test_block_line_numbers_are_one_based():
    blocks = parse("# T\n\nSpoken.\n\n```py\nx\n```\n")
    assert [(b.kind, b.line) for b in blocks] == [("heading", 1), ("para", 3), ("code", 5)]


def test_crlf_is_normalised():
    blocks = parse("# T\r\n\r\nSpoken.\r\n")
    assert [(b.kind, b.text) for b in blocks] == [("heading", "T"), ("para", "Spoken.")]
