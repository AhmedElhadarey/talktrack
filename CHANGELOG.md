# Changelog

## 0.1.1 (2026-10-17)

- **MLX speech:** uses only mlx-audio's model loader and forward pass. Phonemes, chunking, voices and word timings are shared with the ONNX engine, so an mlx-audio release can no longer break them, and both engines time words the same way.
- **Word timings:** after a word misaki cannot pronounce, MLX no longer shifts the later words in that stretch of speech or drops its last word.
- **`espeak` extra:** now used by the ONNX engine too, not only MLX.

## 0.1.0 (2026-10-17)

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

- **Start time and access token:** `?at=90` starts the player 90 s in. `?t=` is only the access token on links served beyond this machine; a token that began with digits used to start the walkthrough part-way through.
- **Revising rewrites the source:** a revised slide's Markdown takes the old slide's place, so the walkthrough's resource and `source.md` show each slide once and build the same slides again.
- **Findings without delay:** problems with the last slide of an `add_slides` call come back in that call's reply, not the next one.
- **Durations on the player's clock:** slide start times and lengths in `finish_walkthrough`, `list_walkthroughs` and `showntell list` match the player, chapter cards and pauses between paragraphs included.
- **Pull request ranges:** `rev=A...B` compares `B` with the commit where it branched from `A`.
- **Stray list markers:** a narration paragraph that starts with `1. ` or `- ` after the slide's visual is spoken, not made into a second visual.
- **Project folder in the reply:** `start_walkthrough` says which folder `file=` and `rev=` read from.
- **Exporting a Markdown file** waits for its audio without drawing every slide in a browser on each check.
- **Theme switch keeps your place** in the walkthrough.
- **Clearer daemon errors:** a plain-text refusal from the daemon reaches the agent as a message, not a JSON error.
- **Pinned installs:** README, plugin and Gemini extension install `@v0.1.0` from GitHub; `scripts/sync_version.py` keeps the pins on the current version. Pushing a tag no longer publishes to PyPI; publishing a GitHub release does, after lint, format and tests pass.
- **Pruning:** `showntell prune --older-than 60d` deletes walkthroughs unchanged for that long and cached audio nothing uses (`--dry-run` lists them); the daemon log stays under 5 MB.
- **Shared folders only:** when the MCP client shares its folders, `project_dir` must be inside one of them.
- **Speech fallback:** on Apple silicon, speech switches to ONNX when MLX fails to load or speak, instead of failing.
- **Windows:** marked untested, best effort.
- **Formatting:** the code is formatted with `ruff format` (line length 140), checked in CI.

- **Drawings that move:** consecutive ```` ```svg step ```` blocks are versions of one drawing; at `#step-2`, elements that keep their id glide to their new place, size, angle and colour on the narration's clock, so seeking and video show the motion exactly.
- **Hand-drawn drawings:** ```` ```svg sketch ```` redraws shapes with Rough.js and text in the Virgil face, as on a whiteboard.
- **Overlap check:** the player reports labels a line runs through, or that sit on each other (`drawing_overlap`).
- **Drawings past their edge:** a drawing with parts outside its viewBox, which spill past the slide, is reported (`overflow`).
- **Row ids:** a table row's `{#id}` is taken from any cell, not only the last.
- **Video keeps the motion:** MP4 export and posters now capture the travelling dot, counting numbers and checklist ticks frame by frame; before, a frame was reused while the slide's cue stayed the same.
- **One-file export:** `showntell export ID talk.html` writes a single page that plays offline from a double-click, with the player, slides, MP3 narration and only the libraries its slides use (a four-minute walkthrough with code comes to about 3 MB, mostly narration; diagrams add about 1 MB and formulas 0.7 MB). It makes no network requests.
- **draw.io diagrams:** ```` ```drawio file=docs/arch.drawio ```` shows a page of a `.drawio` file (plain or compressed) or a `.drawio.svg` export, drawn by the official draw.io viewer, bundled and loaded only when needed, with no network access. Cues point at cells by id or label, shapes reveal in order with edges following their ends, and links, outside images, web fonts and maths are removed first.
- **Drawings:** an ```` ```svg ```` block, inline or read with `file=`, draws what code and boxes can't: light, orbits, lenses, layouts. Elements with ids take cues and reveal in steps, strokes draw themselves in, and theme colours are available as `var(--fg)` and friends. The builder keeps only drawing elements and attributes; anything that could run or fetch is removed and reported (`svg_removed`).
- **Chart callouts:** a note on the tallest bar no longer crowds the title and the value label.

- **Layout checked at finish:** with Playwright installed, `finish_walkthrough` draws every slide fully revealed in headless Chromium, so findings such as `text_too_small` and `overflow` reach the agent before it stops, without anyone opening the link (`SHOWNTELL_FINISH_CHECK=0` turns it off).
- **Shorter guide:** the authoring guide went from about 2,470 to 1,830 words, checked against the old one by A/B evaluation; a chart, stat, chat or tree block that fails to parse now returns its format in the finding.
- **More cue forms:** `#Sender->>Receiver` points at a message in a sequence diagram, and `#row-N` works on tables whose rows have ids.
- **Clearer codes:** problems in chart, stat and chat blocks are `block_error` and `block_too_big` (were `chart_error` and `chart_too_big`).
- **Smoother code steps:** words that stay glide within edited lines, and the scale holds still between versions.
- **Reduced motion and screen readers:** with reduced motion, numbers show their value at once and the travelling dot rests at the end of its route; the route of an edge path and the target of a zoom are announced.
- **Charts in every frame:** charts are drawn to the frame's shape, so portrait and square charts fill the slide.
- **Daemon updates:** a daemon running older code is replaced once idle, even when the version number is the same; a newer one is used as it is, so two installs never replace each other in turn. `showntell stop` stops it.
- **Internals:** the builder is split into cue resolution, checks and Mermaid reading, and the player into modules; each bundled slide kind is one Python module and one JS module (see CONTRIBUTING, "Adding a slide kind").

- **Code that evolves:** consecutive `step` code blocks are versions of one piece of code; `#step-2` morphs to the next, with unchanged lines gliding into place.
- **Long code:** `scroll` keeps up to 400 lines at reading size, scrolls to each cued range and dims the rest.
- **Packets on diagrams:** `#a->b->c` sends a dot along flowchart edges, one hop at a time; edges on step-reveal diagrams draw themselves in.
- **Pointer styles:** `{.circle}`, `{.underline}`, `{.arrow}` and `{.highlight}` after a cue draw hand-drawn marks; `{.zoom}` moves the camera onto the target.
- **Numbers:** a `stat` block shows up to four headline numbers that count from before to after when named.
- **Exchanges:** a `chat` block shows a conversation or request and response, message by message.
- **Images:** `{frame=browser|window|phone}` draws device chrome; cues point at a region with `#@left,top,width,height`.
- **Chart notes:** `note …:` lines add callouts that cues can name.
- **Chapters, stacks, checklists:** `.chapter` headings make numbered part cards; `.stack` draws a list as layers; `[x]`/`[ ]` items make a checklist whose ticks draw in.

- **Structured findings:** every problem returned to the agent has a stable code, a severity, the slide, the Markdown line and a separate fix; replies list errors first, at most twelve, three per code. `docs/spec.md` lists every code.
- **New checks:** narration that reads out handles, URLs or commit hashes; step-reveal cues bunched in one paragraph or out of source order; four code slides in a row; an opening that names code first; diffs of lockfiles and generated files; slides that hold still for over 20 s once voiced. `finish_walkthrough` says which checks could not run and lists slide start times.
- **Player findings:** the open player reports text shrunk below a readable size, content that overflows, cues pointing off the slide, slides slow to draw, and viewers left waiting; errors before the player starts are reported too.
- **Slide-ready wait:** narration for a new slide waits up to 1.5 s for its diagram or formula to finish drawing.
- **New slide kinds:** ```` ```chart ```` (bar, hbar, line), ```` ```terminal ```` and ```` ```tree ````, with cues, step reveals and checks; `showntell://slide-kinds` describes every kind with an example.
- **Themes:** dark, light, paper and high contrast, all meeting WCAG text contrast; chosen by `theme`, front matter, or the project's `showntell.theme.json`, and switchable by the viewer.
- **preview_slides:** a tool that returns pictures of slides, fully revealed, for the agent to check.
- **Ranges and presenting:** `?from=slide&to=slide` plays a range and stops, and `revise_slide` links to the revised slide alone; "Pause after each step"; `?present=1` steps through by hand without the voice, with notes and an audience window; a menu item copies a change request for the current slide.
- **Embedding:** `static/embed.js` defines `<showntell-player>` with events, `play`, `pause`, `seek` and a lazy poster.
- **Captions:** WebVTT phrases break where the voice pauses or a sentence ends.
- **Tests:** golden player state for three walkthroughs, Linux pixel baselines (`scripts/update_baselines.sh`), a contrast test for every theme.
- **Idle cost:** the player's animation loop stops while paused.

- **MP4 export:** `showntell export ID OUT.mp4` captures every frame by seeking the player in headless Chromium: H.264 at 30 fps (`--fps`), 1920x1080 for landscape, AAC narration placed on the timeline, captions as a mov_text track (`--burn-captions` to draw them too), parallel workers (`--workers`), duration checked against the timeline. 13 to 16 times real time on an M-series Mac. Needs the new `video` extra (Playwright) and FFmpeg.
- **Capture mode:** `?capture=1` on the player hides everything but the stage, plays no audio, turns animation off and exposes `window.__showntell.duration()` and `seek(t)`. `showntell.capture.capture_frames` returns PNG frames at given times.
- **Posters:** folder exports write `poster.png`, the title slide, and reference it in `og:image` and `twitter:image` tags, when Playwright is installed.
- **Timeline:** the exporter's paragraph lengths now match the player's exactly for paragraphs without audio.
