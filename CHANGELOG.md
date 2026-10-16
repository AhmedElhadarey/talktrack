# Changelog

## 0.1.0 (unreleased)

First public release.

- **Format:** Markdown walkthroughs with code, diffs, Mermaid diagrams, KaTeX formulas, tables, lists, cards, steps and images; pointer cues as links; step reveals with `.build`.
- **Agents:** MCP tools to start, add slides, revise a slide, finish and list; prompts for diffs, files and topics; problems reported back on every call.
- **Code from disk:** `file=` and `lines=`, and diffs from git revisions, with real line numbers.
- **Speech:** Kokoro-82M on MLX (Apple silicon) or ONNX Runtime, English voices, pronunciation of code, audio cache.
- **Player:** live updates, a pointer timed to the word, captions, transcript, seeking, keyboard control, step reveals, screen-reader announcements.
- **Daemon:** shared across MCP clients through `showntell mcp`; `doctor`, `play`, `export`, `list` and macOS `autostart` commands.
- **Export:** static folders with WebVTT captions and optional MP3 audio.
