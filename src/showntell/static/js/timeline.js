// The timeline: every narration paragraph gets a start time on one clock, and the visible
// state of the player (slide, pointer, step reveals, caption word) is a pure function of time.
// Nothing here touches the DOM, so it is shared by playback, seeking and tests.

export const GAP = 0.3; // silence after a paragraph
export const SLIDE_GAP = 0.6; // silence after a slide's last paragraph
export const WORDS_PER_SECOND = 2.7;

export function estimate(say) {
  if (say.pause) return say.pause;
  if (say.status === "ready" && say.duration) return say.duration;
  return Math.max(1, (say.text || "").split(/\s+/).filter(Boolean).length / WORDS_PER_SECOND);
}

// Build segments for every paragraph: {key, slide, say, start, dur, gap, ready}.
export function build(w) {
  const segs = [];
  let t = 0;
  w.slides.forEach((slide, si) => {
    const says = slide.says.filter((x) => x.pause || x.text);
    if (!says.length) {
      // A slide without narration still gets a moment on screen.
      segs.push({ key: `${slide.id}#empty`, slide: si, say: -1, start: t, dur: 2.5, gap: SLIDE_GAP, ready: true, empty: true });
      t += 2.5 + SLIDE_GAP;
      return;
    }
    says.forEach((x, k) => {
      const j = slide.says.indexOf(x);
      const dur = estimate(x);
      const gap = x.pause ? 0 : k === says.length - 1 ? SLIDE_GAP : GAP;
      const ready = !!x.pause || x.status === "ready" || x.status === "error";
      segs.push({ key: x.id, slide: si, say: j, start: t, dur, gap, ready, pause: !!x.pause, error: x.status === "error" });
      t += dur + gap;
    });
  });
  return { segs, total: t };
}

// Index of the segment playing at global time t (the last one starting at or before t).
export function segmentAt(tl, t) {
  const { segs } = tl;
  if (!segs.length) return -1;
  let lo = 0, hi = segs.length - 1;
  if (t <= 0) return 0;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (segs[mid].start <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

// Cues that have fired on a slide by time t: earlier paragraphs' cues all, the current
// paragraph's up to its local time. Returns them in firing order.
export function firedCues(w, tl, segIndex, local) {
  const seg = tl.segs[segIndex];
  if (!seg) return [];
  const slide = w.slides[seg.slide];
  let first = segIndex;
  while (first > 0 && tl.segs[first - 1].slide === seg.slide) first--;
  const out = [];
  for (let k = first; k <= segIndex; k++) {
    const s = tl.segs[k];
    if (s.say < 0) continue;
    for (const c of slide.says[s.say].cues || []) {
      if (k < segIndex || (c.t ?? 0) <= local + 0.05) out.push({ ...c, _say: s.say, _at: s.start + (c.t ?? 0) });
    }
  }
  return out;
}

// Everything the screen should show at global time t.
export function stateAt(w, tl, t) {
  const i = segmentAt(tl, t);
  if (i < 0) return { seg: -1, slide: -1 };
  const seg = tl.segs[i];
  const local = Math.max(0, t - seg.start);
  const inAudio = local < seg.dur;
  const cues = firedCues(w, tl, i, local);
  const slide = w.slides[seg.slide];
  const lastOfSlide = i === tl.segs.length - 1 || tl.segs[i + 1].slide !== seg.slide;
  // Step reveals finish once the slide's narration is over.
  const revealAll = lastOfSlide && !inAudio;
  let step = 1;
  for (const c of cues) if (c.kind === "step") step = c.n;
  const slideStartT = tl.segs.find((s) => s.slide === seg.slide)?.start ?? 0;
  return {
    seg: i,
    slide: seg.slide,
    say: seg.say,
    local,
    inAudio,
    cue: cues.length ? cues[cues.length - 1] : null,
    cues,
    revealAll,
    reveal: revealLevel(slide, cues, revealAll),
    step, // code that evolves: the version on screen
    sinceSlide: t - slideStartT, // seconds since the slide began
  };
}

// For `.build` slides: how much is revealed, by the kind of part the slide's cues point at.
// {items: n} for parts with ids (list items, chart bars, tree entries, numbers, messages),
// {rows: n} for table rows, {line: n} for one pane of numbered lines (code, terminal),
// {nodes: n} for flowcharts; counted in source order. null when everything shows.
export function revealLevel(slide, cues, all) {
  if (!slide || !(slide.classes || []).includes("build") || all) return null;
  const v = slide.visual || {};
  const every = slide.says.flatMap((x) => x.cues || []);
  const upTo = (kind, ids) => {
    let n = 0;
    for (const c of cues) if (c.kind === kind) n = Math.max(n, ids.indexOf(c.part ?? c.id) + 1); // a nested part reveals its outer one
    return n;
  };
  if (every.some((c) => c.kind === "item")) return { items: upTo("item", v.ids || (v.items || []).map((it) => it.id)) };
  if (every.some((c) => c.kind === "row")) return { rows: upTo("row", v.ids || []) };
  const lineCues = every.filter((c) => c.kind === "lines");
  if (lineCues.length && !v.old) { // a diff's two panes always show whole
    // Show the lines leading up to the first one the narration points at.
    let line = Math.min(...lineCues.map((c) => c.l0)) - 1;
    for (const c of cues) if (c.kind === "lines") line = Math.max(line, c.l1);
    return { line };
  }
  if ((v.type === "flowchart" || v.type === "graph") && every.some((c) => c.kind === "node" || c.kind === "edge")) {
    const order = v.order || [];
    let n = 0;
    for (const c of cues) {
      if (c.kind === "node") n = Math.max(n, order.indexOf(c.id) + 1);
      if (c.kind === "edge") for (const id of c.path) n = Math.max(n, order.indexOf(id) + 1);
    }
    return { nodes: n, order };
  }
  return null;
}

// The word being spoken: index into say.words at local time, or -1.
export function wordAt(say, local) {
  const words = say?.words || [];
  let idx = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].s <= local + 0.02) idx = i; else break;
  }
  return idx;
}

// Start time of a slide (its first segment).
export function slideStart(tl, slideIndex) {
  const s = tl.segs.find((x) => x.slide === slideIndex);
  return s ? s.start : null;
}

// End time of a slide (after its last segment and gap).
export function slideEnd(tl, slideIndex) {
  let end = null;
  for (const s of tl.segs) if (s.slide === slideIndex) end = s.start + s.dur + s.gap;
  return end;
}

// Steps for presenting by hand: the start of every slide, plus, on step-reveal slides, the
// moment each cue reveals another part, and the end where everything shows. [{t, slide, say}]
export function steps(w, tl) {
  const out = [];
  const seen = new Set();
  const add = (t, slide, say) => {
    const key = Math.round(t * 100);
    if (!seen.has(key)) { seen.add(key); out.push({ t, slide, say }); }
  };
  w.slides.forEach((slide, si) => {
    const segs = tl.segs.filter((s) => s.slide === si);
    if (!segs.length) return;
    add(segs[0].start, si, segs[0].say);
    if (!(slide.classes || []).includes("build")) return;
    let last = JSON.stringify(revealLevel(slide, [], false));
    for (let i = 0; i < segs.length; i++) {
      const seg = segs[i];
      if (seg.say < 0) continue;
      for (const c of slide.says[seg.say].cues || []) {
        const t = seg.start + (c.t ?? 0) + 0.06;
        const st = stateAt(w, tl, t);
        const level = JSON.stringify(st.reveal);
        if (level !== last) { last = level; add(t, si, seg.say); }
      }
    }
    // Everything shows once the narration ends, if that happens before the next slide.
    const tail = segs[segs.length - 1];
    const t = tail.start + tail.dur + 0.01;
    if (last !== "null" && tail.gap > 0.05 && stateAt(w, tl, t).slide === si) add(t, si, tail.say);
  });
  return out.sort((a, b) => a.t - b.t);
}

// Per-slide [start, end] spans for the progress bar.
export function slideSpans(tl, nSlides) {
  const spans = Array.from({ length: nSlides }, () => null);
  for (const s of tl.segs) {
    const end = s.start + s.dur + s.gap;
    if (!spans[s.slide]) spans[s.slide] = { start: s.start, end, ready: s.ready };
    else {
      spans[s.slide].end = end;
      spans[s.slide].ready = spans[s.slide].ready && s.ready;
    }
  }
  return spans;
}

export function mmss(s) {
  s = Math.max(0, Math.round(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
