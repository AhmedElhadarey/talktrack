# showntell roadmap

showntell is a local MCP server for narrated slide walkthroughs. A coding agent writes slides and narration; the server voices them on your machine and returns a link that plays within seconds, while the rest is still being written.

Version 1.0 ships when it meets the launch criteria below. Everything else waits until after 1.0.

## Decisions

| Decision | Choice |
|---|---|
| **Name** | showntell. Free on PyPI. The npm name is taken (a dormant React library) and so is the GitHub organisation, so the repo lives under a personal account. The one US registration for SHOWNTELL (Reg. 4700403, narrative-story software) is reported cancelled since March 2022; confirm in USPTO TSDR before launch. A 2015 university screen-recording tool also used the name. |
| **Licence** | MIT |
| **Slide format** | Markdown-based |
| **Speech** | Kokoro-82M, English. MLX on Apple silicon; ONNX (the timestamped export, which returns per-token durations) everywhere else |
| **First link** | returned only once the opening narration is playable |
| **Privacy** | No hosted service and no telemetry. The first run downloads the voice and language models from Hugging Face; nothing else leaves the machine. |

## Launch criteria for 1.0

- **Install:** from an empty cache, install to first link in under 3 minutes on a 100 Mbit/s connection; with models cached, under 10 s.
- **Speed:** the link arrives within 3 s of the start call with the model loaded, on Sonnet.
- **Evaluation:** at least 9 of 10 eval prompts on Sonnet finish with zero render errors and zero stalls at 1× playback.
- **Pointer:** at least 95% of cues resolve to their intended target and fire within 300 ms of the first spoken word of their label, measured against speech-recognition word times.
- **Pronunciation:** at most one mispronounced technical term per minute of narration on the pronunciation test set, measured by speech-recognition round trip.
- **Quality:** no open priority-1 bugs. All tests pass on macOS and on Linux CI with the fake narrator.

## Phase 0: Foundations

### Clean room

- **Reference material:** kept outside the working tree.
- **Provenance check:** runs over the whole tree (README, scripts, tests, static files; vendored libraries excluded).
- **Independent review:** a reviewer who has never seen the reference material reads the spec and all agent-facing text.

### Spec

A short functional spec in our own words, written before any rewrite. It covers:

- **Slide kinds:** every slide kind, including the ones reserved for after 1.0, so the format never needs a breaking change.
- **Pointer cues:** syntax and targets.
- **Frames and limits:** frame sizes and measured limits.
- **Fast first link:** the order of steps that gets the first link out quickly.
- **Install and project root:** the stdio shim, the shared daemon, and how the project root is found.

### Format

- **Markdown layout:** one heading per slide, visuals as fenced blocks, narration as plain paragraphs, and our own pointer-cue syntax. The parser is incremental and lenient.
- **Chunk boundaries:** the end of each append closes every open block except an unclosed code fence, so the opening narration is voiced without waiting for the next chunk. The "any split gives the same result" test stays.
- **Reserved syntax:** citations, pauses, pronunciation overrides, per-slide voice and speed, and camera moves.

### Document model

- **Stable IDs:** each slide is a unit with a stable id, its visual and its narration.
- **Audio files:** named by a hash of model, voice, speed and spoken text, so unchanged narration is never voiced twice.
- **Source:** the raw Markdown source is kept.
- **Versions:** the format and the saved state each carry a version.

### Code read from disk

- **Project root:** from MCP roots, else the stdio shim's working directory, else an explicit argument.
- **Line numbers:** gutters and cues use real file line numbers.
- **Provenance:** a copy of the code and the commit sha are stored with the walkthrough.
- **Git safety:** git runs as plumbing with external diff, textconv and fsmonitor disabled.

### Tools and text

- **Agent-facing text written fresh:** tool names, tool descriptions, server instructions and the authoring guide, written from the spec alone.
- **Format in the tool description:** the essentials needed to write the opening go in the tool description, so the start call stays small. The full guide comes back in the reply to the first append, keeping it off the path to the first link.
- **Tool surface:** structured output (`url`, `status`, `issues`), tool annotations and titles.

### Packaging

- **spaCy model:** downloaded on first run instead of coming from a URL dependency, which PyPI rejects.
- **MLX:** `mlx-audio` is limited to macOS on Apple silicon and pinned exactly. Its private pipeline call sits in one adapter with a test that fails loudly when it breaks; an upstream issue asks for a public API.
- **No GPL in the default install:** misaki's English path runs without the espeak-ng fallback.
- **Vendored libraries:** sha256 pins in the vendor script.

### Rename and hygiene

- **Rename:** package, command, `SHOWNTELL_*` env vars, data folder, launchd label, MCP server name, README.
- **Repo files:** `LICENSE` (MIT), `THIRD_PARTY_NOTICES`, `.gitignore` covering `.env`, and a `.env.example` listing the options.
- **Existing tests:** count as zero, because they test the parser being replaced.

### Exit

The new format runs end to end, the provenance check is clean, and the first commit is made.

## Phase 1: Version 1.0

### Evaluation first

- **Harness:** five to ten prompts on Sonnet, built before the rest of this phase and re-run after every format or guide change.
- **Measures:** time to link, total time, cost, validation issues, render errors, stalls, cue accuracy and mispronunciations.

### Security

- **IDs and keys:** 128-bit walkthrough IDs, with a separate write key.
- **Web routes:** Host and Origin checks on every route, and JSON-only POSTs.
- **Player reports:** each needs a per-walkthrough report key, and is marked untrusted when passed to the agent.
- **Image sources:** limited to the project root.
- **Beyond localhost:** serving to other machines needs an access token.

### Authoring

- **Code from disk:** built to the Phase 0 rules.
- **Revise:** a tool to replace one slide, re-voicing only what changed.
- **Cue inference:** narration with no cues gets them inferred by matching its words to code identifiers, node labels and list titles. A model that ignores the guide still gets a moving pointer.
- **Checks:**
  - **Pacing:** too little narration for the code shown.
  - **Repetition:** narration reading on-screen text word for word.
  - **Sameness:** repeated opening phrases.
- **Walkthrough files:** `showntell play file.md` voices and plays a Markdown walkthrough without an agent.

### Slide kinds

The nine existing kinds (intro, code, diff, diagram, math, image, cards, bullet and numbered lists, table) plus **step reveals**: diagram nodes, list items and code lines appear as they are mentioned.

### Voice

- **Pronunciation of code:** split camelCase and snake_case; read symbols, units, versions, paths and complexity notation aloud; apply a user word list. Captions still show the original text.
- **Backends:** one speech interface with two implementations, MLX and ONNX.
- **Voices:** one voice per walkthrough.

### Player

- **Time-driven renderer:** the slide state at any moment is a pure function of narration time, so seeking and export work the same way as playback.
- **Live updates:** server-sent events send only the slides that changed.
- **Navigation:** seek anywhere including within a slide, transcript panel, keyboard control.
- **Pace:** an optional pause at the end of each slide.
- **Accessibility:** screen-reader announcements for slide changes, captions, reduced-motion mode, and a checked phone layout.

### Install

- **One-line install:** `uvx showntell`. In stdio mode it starts the shared background server or reuses a running one.
- **Daemon:** a lockfile holding pid, port, version and token, a `/health` route, and a restart when idle if versions differ.
- **First run:** first-run download progress and `showntell doctor`.
- **Claude Code:** a plugin with `.mcp.json` and a skill; MCP prompts for diff, file and concept walkthroughs.

### Testing

- **Fake narrator:** fixed timings, so tests run without a GPU.
- **Server tests:** in-process MCP client tests and a restart test.
- **Player tests:** JS module tests and screenshot tests for every slide kind in every frame.

### Exit

All Phase 1 items are done and the evaluation meets the launch criteria.

## Phase 2: Launch

- **Static export and captions:** a self-contained folder that plays from any static host, with a WebVTT captions file.
- **Docs:** README with a demo, format spec, architecture, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`.
- **Gallery:** four exported walkthroughs on GitHub Pages.
- **Release:** a CI workflow (Linux with the fake narrator, plus macOS) and a PyPI trusted-publishing workflow.
- **Exit:** the launch criteria are checked and recorded. Publishing and announcing (Show HN, r/LocalLLaMA, MCP registries) are the owner's call.

## After 1.0

| Area | Items |
|---|---|
| **Slide kinds** | camera (zoom and pan on a cue), scene animation drawn by our own SVG engine, chart, terminal, file tree, quiz, formula-part pointer |
| **Trust** | citations with a sources panel, open-in-editor links, stale-code marks, syntax checks on code blocks |
| **Voice** | Kokoro's other languages (check which give word timings), voice blends, per-slide voice and speed, second speaker, sentence-level audio for slow CPUs, recording your own voice |
| **Sharing** | MP4 export, MP3 and podcast feed, read view, CI recipe for pull requests, embedding, themes |
| **Player** | audio-only mode with lock-screen controls, light theme, live parameters |
| **Product** | courses, teaching styles, asking a question from the player, MCP Apps inline player, retention and compressed audio, generated images |

## Not planned

- **Background music and sound effects:** studies show they reduce learning.
- **A hosted service:** showntell runs on your machine; static export covers sharing.
