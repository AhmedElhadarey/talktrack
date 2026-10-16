// Golden state: what the player shows at fixed times for fixed walkthroughs. A change to the
// timeline that alters any visible state fails here; if the change is intended, regenerate with
// UPDATE_GOLDEN=1 node --test tests/js/ and review the diff. Same result on every OS.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { test } from "node:test";
import { build, stateAt, steps } from "../../src/showntell/static/js/timeline.js";

const dir = new URL("./golden/", import.meta.url);

function snapshot(w) {
  const tl = build(w);
  const states = [];
  for (let t = 0; t <= tl.total + 0.001; t += 0.25) {
    const st = stateAt(w, tl, t);
    const cue = st.cue ? { kind: st.cue.kind, id: st.cue.id, l0: st.cue.l0, l1: st.cue.l1, side: st.cue.side } : null;
    states.push({ t: +t.toFixed(2), slide: st.slide, seg: st.seg, inAudio: st.inAudio, cue, reveal: st.reveal });
  }
  return { total: +tl.total.toFixed(3), steps: steps(w, tl).map((s) => +s.t.toFixed(3)), states };
}

for (const name of readdirSync(dir).filter((f) => f.endsWith(".input.json"))) {
  test(`golden state: ${name}`, () => {
    const w = JSON.parse(readFileSync(new URL(name, dir), "utf8"));
    const got = JSON.parse(JSON.stringify(snapshot(w)));
    const file = new URL(name.replace(".input.json", ".golden.json"), dir);
    if (process.env.UPDATE_GOLDEN) writeFileSync(file, JSON.stringify(got, null, 1) + "\n");
    assert.deepEqual(got, JSON.parse(readFileSync(file, "utf8")));
  });
}
