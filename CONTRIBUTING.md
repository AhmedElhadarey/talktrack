# Contributing

Thanks for helping. showntell is small on purpose: one Python package, one player, no build step.

## Setup

```zsh
uv sync
uv run pytest                          # fast tests; no voice model needed
uv run pytest -m "mlx or onnx or slow" # tests that need models or start processes
uv run ruff check . && uv run ruff format --check .
node --test tests/js/*.test.js         # player logic
```

Run a daemon without a voice model with `uv run showntell serve --speech fake --port 4800`.

## Where things live

| Path | What |
|---|---|
| `src/showntell/format.py` | the incremental Markdown parser |
| `src/showntell/build.py` | slides from parsed blocks: visuals, code from disk, cues, checks |
| `src/showntell/store.py` | walkthrough sessions, speech requests, events, persistence |
| `src/showntell/server.py` | MCP tools, prompts, resources and web routes |
| `src/showntell/app.py` | the daemon: wiring and the request guard |
| `src/showntell/speech/` | speech backends (MLX, ONNX, fake), the worker queue and audio cache |
| `src/showntell/pronounce.py` | turning code and units into speakable text |
| `src/showntell/static/` | the player; `js/timeline.js` is the pure core |
| `src/showntell/text/` | everything the agent reads: instructions, tool descriptions, guide |
| `docs/` | format reference, functional spec, architecture |
| `scripts/eval.py` | end-to-end evaluation with real agents |

## Changes

- **Format changes** go into `docs/format.md` first, keep the "any split at block boundaries" parser property, and must not break existing walkthroughs. The format version lives in `model.py`.
- **Agent-facing text** in `src/showntell/text/` is tuned against the evaluation in `scripts/eval.py`. Re-run the affected prompts and include the before and after table in the pull request.
- **Player changes** need a screenshot of each affected slide kind, and `timeline.js` changes need a `node --test` case.
- **New slide kinds** need a parser test, a builder test, validation messages, a renderer, a screenshot test, and a section in `docs/format.md`.
- **Dependencies:** nothing GPL in the default install. Optional extras may be.

## Releasing

- **One version:** `pyproject.toml` holds it. After changing it, run `uv run scripts/sync_version.py`; it stamps `src/showntell/__init__.py`, the plugin and marketplace manifests and `gemini-extension.json`, and regenerates `GEMINI.md` from the plugin skill. `tests/test_versions.py` fails while anything disagrees.
- **Generated files:** edit `claude-plugin/skills/showntell/SKILL.md` and `claude-plugin/.mcp.json`, never `GEMINI.md` or `gemini-extension.json`, then re-run the script.
- **Changelog:** rename `## X.Y.Z (unreleased)` to `## X.Y.Z (YYYY-MM-DD)` with at least one entry under it.
- **Tag:** push `vX.Y.Z`. `release.yml` stops unless the tag equals the pyproject version, the manifests agree and the changelog section is dated and non-empty; then it builds, smoke-tests the wheel with `scripts/wheel_smoke.sh` and publishes to PyPI.
- **Check first:** `uv run scripts/sync_version.py --check --tag vX.Y.Z` runs the same gate locally.

## Reporting bugs

Include `showntell doctor` output, the walkthrough's `source.md` (in the data folder under `walkthroughs/<id>/`), and what you expected.
