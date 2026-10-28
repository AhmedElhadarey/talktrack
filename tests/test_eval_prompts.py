"""The evaluation runner can ask with an MCP prompt, rendered by the server's own code."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from mcp.client import Client

from talktrack.server import create_mcp, render_prompt

ROOT = Path(__file__).resolve().parents[1]


def _eval():
    spec = importlib.util.spec_from_file_location("eval_runner", ROOT / "scripts" / "eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load(name: str) -> list:
    return json.loads((ROOT / "eval" / name).read_text())


def test_a_plain_entry_is_asked_as_written():
    assert _eval().prompt_text({"id": "x", "prompt": "Use talktrack to explain DNS."}) == "Use talktrack to explain DNS."


def test_an_entry_with_an_mcp_prompt_sends_the_rendered_template():
    entry = {"id": "x", "mcp_prompt": {"name": "diff", "args": {"target": "a..b", "audience": "executive"}}}
    text = _eval().prompt_text(entry)
    assert text == render_prompt("diff", {"target": "a..b", "audience": "executive"})
    assert "changes in a..b" in text and "The audience is executive:" in text


def test_prompt_and_mcp_prompt_together_put_the_prompt_first():
    entry = {"id": "x", "prompt": "Context line.", "mcp_prompt": {"name": "topic", "args": {"topic": "TLS"}}}
    assert _eval().prompt_text(entry).startswith("Context line.\n\n") and "TLS" in _eval().prompt_text(entry)


async def test_the_rendering_is_what_an_mcp_client_receives(service):
    args = {"target": "34b942b~1..34b942b", "spec": "memory stays bounded"}
    async with Client(create_mcp(service)) as client:
        got = (await client.get_prompt("review", args)).messages[0].content.text
    assert got == render_prompt("review", args)


def test_unknown_prompt_names_are_refused():
    with pytest.raises(KeyError, match="nope"):
        render_prompt("nope", {})
    with pytest.raises(KeyError):
        render_prompt("_shared", {})


def test_walkthrough_entries_are_before_after_pairs_and_all_render():
    entries = _load("prompts-walkthrough.json")
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)) == 10
    for stem in ("feature", "course", "bug", "audience", "review-spec"):
        assert f"{stem}-before" in ids and f"{stem}-after" in ids
    assert not set(ids) & {e["id"] for e in _load("prompts.json")}
    for e in entries:
        assert _eval().prompt_text(e).strip()
