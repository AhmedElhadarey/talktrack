# Changelog

## Unreleased

- **MP4 export:** `showntell export ID OUT.mp4` captures every frame by seeking the player in headless Chromium: H.264 at 30 fps (`--fps`), 1920x1080 for landscape, AAC narration placed on the timeline, captions as a mov_text track (`--burn-captions` to draw them too), parallel workers (`--workers`), duration checked against the timeline. 13 to 16 times real time on an M-series Mac. Needs the new `video` extra (Playwright) and FFmpeg.
- **Capture mode:** `?capture=1` on the player hides everything but the stage, plays no audio, turns animation off and exposes `window.__showntell.duration()` and `seek(t)`. `showntell.capture.capture_frames` returns PNG frames at given times.
- **Posters:** folder exports write `poster.png`, the title slide, and reference it in `og:image` and `twitter:image` tags, when Playwright is installed.
- **Timeline:** the exporter's paragraph lengths now match the player's exactly for paragraphs without audio.

## 0.1.0 (unreleased)

First public release.

- **Format:** Markdown walkthroughs with code, diffs, Mermaid diagrams, KaTeX formulas, tables, lists, cards, steps and images; pointer cues as links; step reveals with `.build`; pauses, footnotes and pronunciation overrides.
- **Agents:** MCP tools to start, add slides, revise a slide, finish and list; prompts for diffs, files and topics; resources holding each walkthrough's Markdown; problems reported back on every call; cues inferred when narration has none.
- **Code from disk:** `file=` and `lines=`, and diffs from git revisions, with real line numbers. Git runs only plumbing commands.
- **Speech:** Kokoro-82M on MLX (Apple silicon) or ONNX Runtime (int8, any CPU), 27 English voices, no GPL dependencies by default. Code, units, versions and several hundred technical terms are pronounced properly; unknown words are never skipped.
- **Player:** live per-slide updates, a pointer timed to the word, captions, transcript, chapters, seeking, keyboard control, pause after each slide, step reveals, screen-reader announcements, reduced motion, phone layout. Missing audio plays silently instead of stopping.
- **Daemon:** shared across MCP clients through `showntell mcp`; lockfile, health route, replacement when idle on upgrade; `doctor`, `play`, `export`, `list` and macOS `autostart` commands.
- **Export:** static folders with WebVTT captions and optional MP3 audio; a gallery builder; a recorder for MP4 and GIF demos.
- **Install paths:** a Claude Code plugin marketplace in this repository, a Gemini CLI extension, and one version stamped into every manifest by `scripts/sync_version.py`.
- **Security:** Host and Origin checks on every route, JSON-only POSTs, 128-bit ids with separate write and report keys, access tokens beyond localhost.
