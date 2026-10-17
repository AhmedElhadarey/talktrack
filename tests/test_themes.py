"""Themes: every theme defines every token, text meets WCAG contrast, and project overrides are checked."""

from __future__ import annotations

import json
import re

import pytest

from showntell import themes
from showntell.server import render_player

CSS = themes.CSS.read_text()


def block(name: str) -> dict[str, str]:
    sel = ':root, [data-theme="dark"]' if name == "dark" else f'[data-theme="{name}"]'
    i = CSS.index(sel + " {")
    body = CSS[i : CSS.index("}", i)]
    return dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", body))


def lum(hex_: str) -> float:
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


# (foreground, background, minimum): 4.5 for text, 3 for large text and interface marks.
PAIRS = [
    ("fg", "stage", 7),
    ("fg", "panel", 4.5),
    ("fg", "node", 4.5),
    ("muted", "stage", 4.5),
    ("muted", "panel", 4.5),
    ("caption", "bg", 4.5),
    ("code-fg", "code-bg", 7),
    ("on-accent", "accent", 4.5),
    ("accent", "stage", 3),
    ("node-line", "stage", 3),
    ("ln", "code-bg", 3),
    ("add-fg", "code-bg", 3),
    ("del-fg", "code-bg", 3),
    ("danger", "bg", 3),
] + [(f"hl-{k}", "code-bg", 4.5) for k in ("keyword", "title", "attr", "string", "built-in", "comment", "name", "section", "bullet")]


@pytest.mark.parametrize("theme", themes.THEMES)
def test_every_theme_defines_every_token(theme):
    assert set(block(theme)) >= set(themes.token_names()) | {"scheme"}


@pytest.mark.parametrize("theme", themes.THEMES)
@pytest.mark.parametrize("fg, bg, minimum", PAIRS)
def test_text_contrast(theme, fg, bg, minimum):
    t = block(theme)
    ratio = contrast(t[fg], t[bg])
    assert ratio >= minimum, f"{theme}: {fg} on {bg} is {ratio:.2f}:1, needs {minimum}:1"


def test_player_css_has_no_raw_colours():
    css = (themes.CSS.parent / "player.css").read_text()
    raw = [ln for ln in css.splitlines() if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", ln)]
    assert not raw, raw


def test_project_theme_file(tmp_path):
    (tmp_path / themes.PROJECT_FILE).write_text(
        json.dumps({"theme": "light", "tokens": {"accent": "#0969da", "--fg": "rgb(10, 10, 10)", "nope": "#fff", "bg": "red; x: y"}})
    )
    theme, tokens, problems = themes.project_theme(str(tmp_path))
    assert theme == "light" and tokens == {"accent": "#0969da", "fg": "rgb(10, 10, 10)"}
    assert any("unknown token `nope`" in p for p in problems) and any("`bg` must be a colour" in p for p in problems)


def test_bad_project_theme_file_is_reported_not_fatal(tmp_path):
    (tmp_path / themes.PROJECT_FILE).write_text("{not json")
    theme, tokens, problems = themes.project_theme(str(tmp_path))
    assert (theme, tokens) == (None, {}) and "not valid JSON" in problems[0]


def test_player_page_carries_theme_and_escaped_tokens():
    page = render_player("T", "landscape", "", "static", live=False, theme="paper", tokens={"accent": "#123456", "x": '"><script>'})
    assert 'data-theme="paper"' in page and 'style="--accent: #123456"' in page and '<script>"' not in page
    assert 'data-theme="dark"' in render_player("T", "landscape", "", "static", live=False, theme="bogus")


async def test_start_uses_the_theme_argument_then_the_project_then_the_default(service, store, tmp_path):
    (tmp_path / themes.PROJECT_FILE).write_text(json.dumps({"theme": "paper", "tokens": {"accent": "#a3410f"}}))
    a = await service.start("A", "landscape", "", 1.0, str(tmp_path), "contrast")
    b = await service.start("B", "landscape", "", 1.0, str(tmp_path))
    c = await service.start("C", "landscape", "", 1.0, None)
    get = lambda r: store.by_handle(r["handle"]).w  # noqa: E731
    assert (get(a).theme, get(b).theme, get(c).theme) == ("contrast", "paper", "dark")
    assert get(b).tokens == {"accent": "#a3410f"}


def test_front_matter_theme(build):
    w, b = build("---\ntheme: light\n---\n# T\n\nHello.\n")
    assert w.theme == "light"
    w, b = build("---\ntheme: neon\n---\n# T\n\nHello.\n")
    assert w.theme == "dark" and any(i.code == "front_matter" for i in b.issues)
