# TODO

Parked work, in no order. Each item says what would settle it.

- **`.morph` adoption.** Four eval rounds and a guide nudge, and no agent has used it. Either add a worked before-and-after example to the guide and test it with one eval prompt built around a state change, or drop the nudge so the guide stays short.
- **Browser by default.** The base install has no Chromium, so the finish-time layout check, slide pictures, poster and MP4 export are off until the user installs with the `video` extra. Decide whether Playwright becomes a base dependency, with the daemon fetching Chromium on first need as it does the voice model.
- **Diagram layout with ELK.** Mermaid's default layout leaves some flowcharts small or tangled; the ELK layout may draw them better.
- **Eval history in the repo.** `eval/results/` is gitignored; a round-by-round summary table in `eval/README.md` would keep the numbers.
