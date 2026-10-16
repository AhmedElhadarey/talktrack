// The showntell player. One clock drives everything: the audio of the current paragraph while
// it plays, wall time during pauses between paragraphs. Each frame computes the state for the
// current time (timeline.js) and brings the screen in line with it.

import { build, mmss, segmentAt, slideEnd, slideSpans, slideStart, stateAt, steps, wordAt } from "./timeline.js";
import { diagramEdges, diagramNodes, fit, renderSlide, sizeDiagram, textRange } from "./render.js";
import { esc, norm } from "./util.js";

const $ = (s) => document.querySelector(s);
const body = document.body;
const LIVE = body.dataset.live === "1";
const REPORT_KEY = body.dataset.reportKey || "";
const params = new URLSearchParams(location.search);
const PRESENT = params.get("present") === "1"; // no voice; step through with notes
const AUDIENCE = params.get("audience") === "1"; // follows a presenter window
const EMBEDDED = window.parent !== window;
window.__showntellStarted = true;
const FRAMES = { landscape: [1280, 720], square: [960, 960], portrait: [720, 1280] };

const stage = $("#stage"), host = $("#slide-host"), pointer = $("#pointer");
const audio = new Audio();
audio.preload = "auto";
const prefetch = new Audio();
prefetch.preload = "auto";

// Read by the evaluation harness and the video recorder.
const metrics = { stalls: 0, stallSeconds: 0, renderErrors: [], ended: false, starts: [], startDelay: null, holds: 0 };
window.__showntell = metrics;

let W = null; // walkthrough data
let TL = { segs: [], total: 0 };
let pos = { seg: 0, local: 0 };
let playing = false, started = false, waitingSince = 0, lastFrame = 0, ended = false;
let shown = { slide: -1, rev: -1, id: "" }; // what is on screen
let view = null; // {parts, ready, root, body}
let lastCueKey = "", lastReveal = "", lastSay = "", lastWord = -2;
let rate = 1, captionsOn = true, pauseAfterSlide = false, pauseSteps = false;
let range = null; // {start, end} in seconds while playing a range of slides
let holdSince = 0; // waiting for a slide to finish drawing before its narration starts
let playPressed = 0;
const HOLD_MAX_MS = 1500, HOLD_SHOW_MS = 150;
let seekingAudio = false;
const failedAudio = new Set(); // audio URLs that failed to load; played silently instead

const store = {
  get(k, d) { try { const v = localStorage.getItem(`showntell.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`showntell.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
rate = store.get("rate", 1);
captionsOn = store.get("cc", true);
pauseAfterSlide = store.get("pauseAfterSlide", false);
pauseSteps = store.get("pauseSteps", false);

// ------------------------------------------------------------------- reporting

const reported = new Set();
// Send a finding to the server, which passes it to the agent on its next call.
function report(message, code = "player_error", slide = "") {
  if (code === "render_error" || code === "player_error" || code === "audio_error") metrics.renderErrors.push(message);
  (metrics.findings ||= []).push({ code, slide, message });
  if (!LIVE || reported.has(message)) return;
  reported.add(message);
  fetch("report", { method: "POST", headers: { "Content-Type": "application/json", "X-Report-Key": REPORT_KEY }, body: JSON.stringify({ message, code, slide }) }).catch(() => {});
}
window.addEventListener("error", (e) => report(`Player error: ${e.message}`));
for (const m of window.__showntellEarly || []) report(`Before the player started: ${m}`);

// Events for a page that embeds the player in an iframe (see embed.js), and its commands.
let lastTimeEvent = 0;
function emit(type, detail = {}) {
  if (EMBEDDED) window.parent.postMessage({ source: "showntell", type, ...detail }, "*");
}
window.addEventListener("message", (e) => {
  const m = e.data;
  if (!m || m.target !== "showntell" || e.source !== window.parent) return;
  if (m.type === "play") play();
  else if (m.type === "pause") pause();
  else if (m.type === "seek" && Number.isFinite(m.t)) seek(m.t);
});

// ------------------------------------------------------------------- layout

function scale() { return parseFloat(stage.dataset.scale) || 1; }

function layoutStage() {
  if (!W) return;
  const [w, h] = FRAMES[W.frame] || FRAMES.landscape;
  const vp = $("#viewport").getBoundingClientRect();
  const pad = vp.width < 640 ? 6 : 16;
  const k = Math.max(0.1, Math.min((vp.width - pad * 2) / w, (vp.height - pad * 2) / h));
  stage.style.width = `${w}px`;
  stage.style.height = `${h}px`;
  stage.style.transform = `scale(${k})`;
  stage.style.left = `${(vp.width - w * k) / 2}px`;
  stage.style.top = `${(vp.height - h * k) / 2}px`;
  stage.dataset.scale = k;
  lastCueKey = ""; // re-place the pointer at the new scale
}
new ResizeObserver(() => { layoutStage(); render(true); }).observe($("#viewport"));

// ------------------------------------------------------------------- data

function setData(w) {
  failedAudio.clear();
  const keepKey = TL.segs[pos.seg]?.key;
  const keepSlideId = W && TL.segs[pos.seg] ? W.slides[TL.segs[pos.seg].slide]?.id : null;
  const first = !W;
  W = w;
  TL = build(W);
  if (first) {
    body.classList.add(W.frame);
    document.title = W.title;
    layoutStage();
  }
  // Keep the playback position on the same paragraph, or the same slide if it was revised.
  let i = keepKey ? TL.segs.findIndex((s) => s.key === keepKey) : 0;
  if (i < 0 && keepSlideId) {
    i = TL.segs.findIndex((s) => W.slides[s.slide]?.id === keepSlideId);
    pos.local = 0;
  }
  pos.seg = Math.max(0, i);
  const cur = W.slides[TL.segs[pos.seg]?.slide];
  if (cur && cur.id === shown.id && cur.rev !== shown.rev) shown.slide = -1; // revised: redraw
  renderChrome();
  if (!started) showOverlay();
  render(true);
  if (range) range.end = slideEnd(TL, range.to) ?? TL.total;
  if (PRESENT) presenterSync();
  emit("durationchange", { duration: TL.total, slides: W.slides.length, finished: W.status === "finished" });
}

function mergeDelta(d) {
  const byId = new Map(W.slides.map((s) => [s.id, s]));
  for (const s of d.slides) byId.set(s.id, s);
  setData({ ...W, status: d.status, title: d.title, questions: d.questions, notes: d.notes, slides: d.order.map((id) => byId.get(id)).filter(Boolean) });
}

async function load() {
  const r = await fetch("data.json", { cache: "no-store" });
  setData(await r.json());
  if (LIVE && W.status !== "finished") connect();
  else if (LIVE) connect();
  if (params.get("from") || params.get("to")) setRange(params.get("from"), params.get("to"));
  const t = parseFloat(params.get("t"));
  if (t > 0) seek(t);
  if (PRESENT) startPresenting();
  if (AUDIENCE) followPresenter();
  emit("ready", { duration: TL.total, slides: W.slides.length, title: W.title, frame: W.frame });
  if (params.get("autoplay") === "1") play();
}

let es = null;
function connect() {
  es = new EventSource("events");
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "full") setData(msg.walkthrough);
    else if (msg.type === "delta") mergeDelta(msg);
    $("#status").classList.remove("offline");
  };
  es.onerror = () => { $("#status").textContent = "Reconnecting…"; $("#status").classList.add("offline"); };
}

// ------------------------------------------------------------------- the clock

function currentSeg() { return TL.segs[pos.seg]; }
function now() { const s = currentSeg(); return s ? s.start + pos.local : 0; }
function sayOf(seg) { return seg && seg.say >= 0 ? W.slides[seg.slide].says[seg.say] : null; }

// The loop runs only while playing, so a paused player (or many on one page) costs nothing.
let looping = false;
function frame(ts) {
  const dt = lastFrame ? Math.min(0.25, (ts - lastFrame) / 1000) : 0;
  lastFrame = ts;
  if (!W || !playing) { looping = false; return; }
  requestAnimationFrame(frame);
  const segBefore = pos.seg;
  advance(dt);
  if (pauseSteps && pos.seg !== segBefore && revealedIn(segBefore) && TL.segs[pos.seg]?.slide === TL.segs[segBefore].slide) pause();
  render(false);
  if (EMBEDDED && ts - lastTimeEvent > 250) { lastTimeEvent = ts; emit("timeupdate", { currentTime: now(), duration: TL.total }); }
}
function kick() {
  if (looping) return;
  looping = true;
  lastFrame = 0;
  requestAnimationFrame(frame);
}

// "Pause after each step": on a step-reveal slide, a paragraph whose cues revealed a part.
function revealedIn(i) {
  const seg = TL.segs[i];
  const slide = seg && W.slides[seg.slide];
  if (!slide || !(slide.classes || []).includes("build") || seg.say < 0) return false;
  return (slide.says[seg.say].cues || []).some((c) => ["item", "row", "lines", "node"].includes(c.kind));
}

function advance(dt) {
  const seg = currentSeg();
  if (!seg) return stall(W.status === "finished" ? "end" : "writing");
  if (!seg.ready) {
    audio.pause();
    return stall("voicing");
  }
  if (range && now() >= range.end - 0.02) return endRange();
  if (holdForSlide(seg)) return;
  const atEnd = pos.seg === TL.segs.length - 1 && pos.local >= seg.dur + seg.gap;
  if (atEnd && W.status !== "finished") return stall("writing"); // caught up with the writer
  unstall();
  const say = sayOf(seg);
  const hasAudio = say && say.audio && !seg.pause && !seg.error && !failedAudio.has(say.audio);
  if (hasAudio && pos.local < seg.dur) {
    const url = say.audio;
    if (!audio.src.endsWith(url)) {
      audio.src = url;
      audio.playbackRate = rate;
      seekingAudio = true;
      audio.addEventListener("loadedmetadata", () => { audio.currentTime = pos.local; seekingAudio = false; }, { once: true });
      audio.play().catch(onBlocked);
      prefetchNext();
      return;
    }
    if (seekingAudio) return;
    if (audio.paused && !audio.ended) audio.play().catch(onBlocked);
    pos.local = audio.ended ? seg.dur : audio.currentTime;
    return;
  }
  if (seg.error && say) report(`Narration on slide ${W.slides[seg.slide].id} could not be voiced: ${say.error || "unknown error"}`, "audio_error", W.slides[seg.slide].id);
  pos.local += dt * rate;
  if (pos.local >= seg.dur + seg.gap) {
    const next = TL.segs[pos.seg + 1];
    if (pauseAfterSlide && next && next.slide !== seg.slide) {
      pos = { seg: pos.seg + 1, local: 0 };
      pause();
      return;
    }
    if (!next) {
      pos.local = seg.dur + seg.gap;
      if (W.status === "finished") return finish();
      return stall("writing");
    }
    pos = { seg: pos.seg + 1, local: 0 };
  }
}

// At the start of a slide, wait (briefly) until it has finished drawing, so the first cue
// lands on a laid-out diagram or formula rather than on an empty box.
function holdForSlide(seg) {
  const firstOfSlide = pos.local === 0 && TL.segs[pos.seg - 1]?.slide !== seg.slide;
  const drawing = firstOfSlide && shown.slide === seg.slide && view && !view.done;
  if (!drawing) {
    if (holdSince) { holdSince = 0; if (!waitingSince) $("#waiting").hidden = true; }
    return false;
  }
  const t = performance.now();
  if (!holdSince) { holdSince = t; metrics.holds += 1; }
  if (t - holdSince > HOLD_SHOW_MS && !waitingSince) {
    $("#waiting").hidden = false;
    $("#waiting-text").textContent = "Drawing the slide…";
  }
  if (t - holdSince < HOLD_MAX_MS) return true;
  const id = W.slides[seg.slide].id;
  report(`Slide ${id} took more than ${HOLD_MAX_MS / 1000} s to draw; its narration started before it was ready.`, "slow_render", id);
  view.done = true; // stop waiting for this one
  holdSince = 0;
  if (!waitingSince) $("#waiting").hidden = true;
  return false;
}

function prefetchNext() {
  for (let i = pos.seg + 1; i < Math.min(TL.segs.length, pos.seg + 3); i++) {
    const s = sayOf(TL.segs[i]);
    if (s?.audio) { if (!prefetch.src.endsWith(s.audio)) prefetch.src = s.audio; return; }
  }
}

function onBlocked() {
  // Autoplay blocked: wait for a click.
  emit("playbackerror", { reason: "blocked" });
  playing = false;
  setPlayIcon();
  showOverlay();
}

let stallWhy = "";
function stall(why) {
  if (why === "end") return finish();
  if (!waitingSince) {
    waitingSince = performance.now();
    stallWhy = why;
    if (started) metrics.stalls += 1;
    emit("waiting", { reason: why });
  }
  $("#waiting").hidden = false;
  $("#waiting-text").textContent = why === "voicing" ? "Voicing the next part…" : "Writing the next slide…";
}

function unstall() {
  if (!waitingSince) return;
  const waited = (performance.now() - waitingSince) / 1000;
  metrics.stallSeconds += waited;
  waitingSince = 0;
  $("#waiting").hidden = true;
  const seg = currentSeg();
  if (started && waited >= 1.5 && seg) {
    const id = W.slides[seg.slide].id;
    report(`The viewer caught up and waited ${waited.toFixed(1)} s for slide ${id} to be ${stallWhy === "voicing" ? "voiced" : "written"}.`
      + (stallWhy === "voicing" ? " Send shorter slides early on." : " Send the next slides sooner, or fewer per call."), "viewer_waited", id);
  }
}

function finish() {
  if (ended) return;
  ended = true;
  metrics.ended = true;
  emit("ended");
  pause();
  unstall();
  showEnd();
}

function play() {
  if (!W || !TL.segs.length) return;
  if (!TL.segs[0].ready && !started) return;
  if (ended) { ended = false; pos = { seg: 0, local: 0 }; $("#end").hidden = true; }
  if (PRESENT || AUDIENCE) return; // stepped by hand, or by the presenter window
  if (range && now() >= range.end - 0.05) seek(range.start);
  if (!started) playPressed = performance.now();
  started = true;
  playing = true;
  $("#overlay").hidden = true;
  $("#range-note").hidden = true;
  setPlayIcon();
  render(true);
  kick();
  emit("play");
}

function pause() {
  const was = playing;
  playing = false;
  audio.pause();
  setPlayIcon();
  if (was) emit("pause");
}

// ------------------------------------------------------------------- playing a range of slides

function setRange(fromId, toId) {
  const idx = (id, d) => { const i = id ? W.slides.findIndex((s) => s.id === id) : -1; return i >= 0 ? i : d; };
  const a = idx(fromId, 0), b = Math.max(a, idx(toId, W.slides.length - 1));
  const start = slideStart(TL, a);
  if (start === null) return;
  range = { start, end: slideEnd(TL, b) ?? TL.total, from: a, to: b };
  seek(start);
}

function endRange() {
  const end = range.end;
  range = null;
  pause();
  seek(Math.max(0, end - 0.05));
  $("#range-note").hidden = false;
  emit("ended", { range: true });
}
$("#range-all").onclick = () => { $("#range-note").hidden = true; play(); };

function toggle() { playing ? pause() : play(); }

function seek(t) {
  if (!TL.segs.length) return;
  t = Math.max(0, Math.min(t, TL.total - 0.01));
  const i = segmentAt(TL, t);
  pos = { seg: i, local: t - TL.segs[i].start };
  ended = false;
  $("#end").hidden = true;
  const say = sayOf(TL.segs[i]);
  if (say?.audio && audio.src.endsWith(say.audio) && pos.local < TL.segs[i].dur) {
    audio.currentTime = pos.local;
  } else {
    audio.pause();
    audio.removeAttribute("src");
  }
  render(true);
  if (PRESENT) presenterSync();
}

function seekSlide(delta) {
  const s = currentSeg();
  if (!s) return;
  let target = s.slide + delta;
  if (delta < 0 && pos.local > 1.5 && TL.segs.findIndex((x) => x.slide === s.slide) === pos.seg) target = s.slide;
  target = Math.max(0, Math.min(W.slides.length - 1, target));
  const t = slideStart(TL, target);
  if (t !== null) seek(t);
}

// ------------------------------------------------------------------- rendering the state

function render(force) {
  if (!W || !TL.segs.length) return;
  const st = stateAt(W, TL, now());
  if (st.slide < 0) return;
  const slide = W.slides[st.slide];
  if (force || shown.slide !== st.slide || shown.rev !== slide.rev || shown.id !== slide.id) {
    if (shown.slide !== st.slide || shown.rev !== slide.rev || shown.id !== slide.id) showSlide(st.slide);
  }
  applyVisual(st, slide, force);
  applyCaption(st, slide, force);
  updateTime(st);
}

function showSlide(i) {
  const slide = W.slides[i];
  shown = { slide: i, rev: slide.rev, id: slide.id };
  lastCueKey = lastReveal = "";
  clearMarks();
  const v = renderSlide(slide, { frame: W.frame, report });
  host.replaceChildren(v.root);
  // The slide eases in from a few pixels below; measure the pointer again once it has landed.
  v.root.addEventListener("animationend", () => { if (view?.root === v.root) { lastCueKey = ""; render(true); } }, { once: true });
  const bodyEl = v.root.querySelector(".slide-body");
  view = { ...v, body: bodyEl, done: false };
  v.ready.then(() => document.fonts?.ready).then(() => {
    if (view.root !== v.root) return;
    if (bodyEl) {
      if (slide.kind === "diagram") sizeDiagram(view.parts.svg, bodyEl);
      else fit(bodyEl);
    }
    if (window.CSS?.highlights && view.parts.wordRanges.length) {
      CSS.highlights.set("diff-del", new Highlight(...view.parts.wordRanges.filter(([s]) => s === "old").map(([, r]) => r)));
      CSS.highlights.set("diff-add", new Highlight(...view.parts.wordRanges.filter(([s]) => s === "new").map(([, r]) => r)));
    }
    view.done = true;
    lastCueKey = lastReveal = "";
    render(true);
    const idle = window.requestIdleCallback || ((f) => setTimeout(f, 50));
    idle(() => { if (view?.root === v.root) measure(slide, bodyEl, view.parts); });
  });
  announce(`Slide ${i + 1} of ${W.slides.length}: ${slide.title}`);
  markChapter(i);
  emit("slidechange", { index: i, id: slide.id, title: slide.title });
}

// Report what the layout did to the slide: text shrunk too far to read, or content that still
// does not fit. Measured once a slide has settled, in stage pixels.
const READABLE_PX = 15; // smallest comfortable text on the 1280-wide stage
function measure(slide, bodyEl, parts) {
  if (!bodyEl || !LIVE) return;
  const content = bodyEl.firstElementChild;
  if (!content) return;
  const id = slide.id;
  if (slide.kind === "diagram" && parts.svg) {
    const vb = parts.svg.viewBox.baseVal;
    const k = vb && vb.width ? parseFloat(parts.svg.getAttribute("width")) / vb.width : 1;
    const label = parts.svg.querySelector(".nodeLabel, text");
    const px = (label ? parseFloat(getComputedStyle(label).fontSize) : 20) * k;
    if (px && px < READABLE_PX - 3) {
      report(`Slide ${id}: the diagram had to shrink until its labels are about ${Math.round(px)} px high, too small to read.`
        + " Split it, or use fewer nodes or a top-to-bottom layout.", "text_too_small", id);
    }
    return;
  }
  const k = parseFloat(content.dataset.scale || "1");
  const sample = content.querySelector(".tx, td, .entry-text p, .entry-text h3, .katex") || content;
  const px = parseFloat(getComputedStyle(sample).fontSize) * k;
  if (k < 0.999 && px < READABLE_PX) {
    report(`Slide ${id}: the ${slide.kind} was shrunk to ${Math.round(k * 100)}% to fit, so its text is about ${Math.round(px)} px high.`
      + " Show less on this slide or split it.", "text_too_small", id);
  }
  const b = bodyEl.getBoundingClientRect(), c = content.getBoundingClientRect(), s = scale();
  if ((c.width - b.width) / s > 4 || (c.height - b.height) / s > 4) {
    report(`Slide ${id}: the ${slide.kind} does not fit the slide even at the smallest size.`, "overflow", id);
  }
}

function clearMarks() {
  pointer.hidden = true;
  if (window.CSS?.highlights) CSS.highlights.clear();
}

function applyVisual(st, slide, force) {
  if (!view?.done) return;
  const revealKey = JSON.stringify(st.reveal);
  if (force || revealKey !== lastReveal) {
    lastReveal = revealKey;
    applyReveal(st.reveal, slide);
  }
  const cueKey = st.cue ? `${st.cue._say}:${st.cue.at}:${st.cue.kind}:${st.cue.id ?? ""}:${st.cue.l0 ?? ""}` : "none";
  if (!force && cueKey === lastCueKey) return;
  lastCueKey = cueKey;
  host.querySelectorAll(".active").forEach((e) => e.classList.remove("active"));
  host.querySelectorAll(".has-active").forEach((e) => e.classList.remove("has-active"));
  host.querySelectorAll(".ln.hl").forEach((e) => e.classList.remove("hl"));
  if (window.CSS?.highlights) CSS.highlights.delete("ptr");
  if (!st.cue) { pointer.hidden = true; return; }
  const target = cueTarget(st.cue, slide);
  if (!target) { pointer.hidden = true; return; }
  placePointer(target.rects, target.wide);
}

function applyReveal(reveal, slide) {
  const p = view.parts;
  const veil = (el, on) => el && el.classList.toggle("veiled", on);
  if (!reveal) {
    host.querySelectorAll(".veiled").forEach((e) => e.classList.remove("veiled"));
    return;
  }
  if ("items" in reveal) p.items.forEach((el) => veil(el, +el.dataset.idx >= reveal.items));
  if ("rows" in reveal) p.rows.forEach((el) => veil(el, +el.dataset.idx >= reveal.rows));
  if ("line" in reveal) p.lines.new.forEach((el, n) => veil(el, n > reveal.line));
  if ("nodes" in reveal && p.svg) {
    const visible = new Set(reveal.order.slice(0, reveal.nodes));
    diagramNodes(p.svg).forEach((g, id) => veil(g, !visible.has(id)));
    for (const e of diagramEdges(p.svg, reveal.order)) {
      const hide = !(visible.has(e.from) && visible.has(e.to));
      veil(e.el, hide);
      veil(e.label, hide);
    }
  }
}

// Where a cue points: a list of DOMRect-like boxes, plus side effects (active classes, highlights).
function cueTarget(cue, slide) {
  const p = view.parts;
  const range = (r) => { if (window.CSS?.highlights) CSS.highlights.set("ptr", new Highlight(r)); };
  if (cue.kind === "lines") {
    const map = p.lines[cue.side || "new"] || p.lines.new;
    const els = [];
    for (let n = cue.l0; n <= cue.l1; n++) if (map.get(n)) els.push(map.get(n));
    if (!els.length) return null;
    if (cue.text) {
      for (const ln of els) {
        const r = findText(ln.querySelector(".tx"), cue.text);
        if (r) { ln.classList.add("hl"); range(r); return { rects: [...r.getClientRects()] }; }
      }
    }
    els.forEach((e) => e.classList.add("hl"));
    return { rects: els.map((e) => e.querySelector(".tx").getBoundingClientRect()) };
  }
  if (cue.kind === "item" || cue.kind === "row") {
    const e = p.items.get(cue.id) || p.rows.get(cue.id);
    if (!e) return null;
    e.classList.add("active");
    e.parentElement.classList.add("has-active");
    if (cue.kind === "item") return { rects: e instanceof SVGElement ? [e.getBoundingClientRect()] : [] }; // a card's own highlight is the pointer
    return { rects: [e.getBoundingClientRect()] };
  }
  if (cue.kind === "node") {
    const g = diagramNodes(p.svg).get(cue.id) || findInDiagram(p.svg, cue.text || cue.id);
    if (!g) return wholeVisual();
    g.classList.add("active");
    return { rects: [g.getBoundingClientRect()] };
  }
  if (cue.kind === "text" && cue.text) {
    if (p.svg) {
      const g = findInDiagram(p.svg, cue.text);
      if (g) { g.classList.add("active"); return { rects: [g.getBoundingClientRect()] }; }
    }
    for (const side of ["new", "old"]) {
      for (const ln of p.lines[side].values()) {
        const r = findText(ln.querySelector(".tx"), cue.text);
        if (r) { ln.classList.add("hl"); range(r); return { rects: [...r.getClientRects()] }; }
      }
    }
    for (const e of [...p.items.values(), ...p.rows.values()]) {
      if (norm(e.textContent).includes(norm(cue.text))) {
        e.classList.add("active");
        e.parentElement.classList.add("has-active");
        return { rects: [e.getBoundingClientRect()] };
      }
    }
    // KaTeX keeps an invisible MathML copy first; search only what is drawn.
    const drawn = p.visual?.querySelector?.(".katex-html") || p.visual;
    const r = drawn && findText(drawn, cue.text);
    if (r) { range(r); return { rects: [...r.getClientRects()] }; }
  }
  return wholeVisual();
}

function wholeVisual() {
  const v = view.parts.visual;
  if (!v || shown.slide < 0 || W.slides[shown.slide].kind === "title") return null;
  const t = v.querySelector?.(".katex-display") || view.parts.svg || v;
  return { rects: [t.getBoundingClientRect()], wide: true };
}

function findText(container, text) {
  if (!container || !text) return null;
  const hay = container.textContent;
  const i = hay.indexOf(text);
  if (i >= 0) return textRange(container, i, i + text.length);
  // Tolerate differences in spacing: search a collapsed copy, keeping a map back to offsets.
  let flat = "";
  const map = [];
  for (let k = 0; k < hay.length; k++) {
    const space = /\s/.test(hay[k]);
    if (space && (flat.endsWith(" ") || !flat)) continue;
    flat += space ? " " : hay[k];
    map.push(k);
  }
  const want = text.replace(/\s+/g, " ").trim();
  const j = flat.indexOf(want);
  if (j < 0 || !want) return null;
  return textRange(container, map[j], map[j + want.length - 1] + 1);
}

function findInDiagram(svg, label) {
  if (!svg || !label) return null;
  const want = norm(label);
  const groups = [...svg.querySelectorAll("g.node, g.edgeLabel, g.cluster, g.actor, text.actor, text.messageText, g.stateGroup")];
  const exact = groups.find((g) => norm(g.textContent) === want);
  if (exact) return exact.closest("g.node, g.edgeLabel, g.cluster, g.stateGroup") || exact;
  const part = groups.find((g) => norm(g.textContent).includes(want));
  return part ? part.closest("g.node, g.edgeLabel, g.cluster, g.stateGroup") || part : null;
}

function placePointer(rects, wide) {
  rects = rects.filter((r) => r && (r.width || r.height));
  if (!rects.length) { pointer.hidden = true; return; }
  const l = Math.min(...rects.map((r) => r.left)), t = Math.min(...rects.map((r) => r.top));
  const r = Math.max(...rects.map((r) => r.right)), b = Math.max(...rects.map((r) => r.bottom));
  const sr = stage.getBoundingClientRect(), k = scale(), pad = wide ? 12 : 6;
  pointer.style.left = `${(l - sr.left) / k - pad}px`;
  pointer.style.top = `${(t - sr.top) / k - pad}px`;
  pointer.style.width = `${(r - l) / k + pad * 2}px`;
  pointer.style.height = `${(b - t) / k + pad * 2}px`;
  pointer.classList.toggle("wide", !!wide);
  pointer.hidden = false;
  const [w, h] = FRAMES[W.frame] || FRAMES.landscape;
  const x = (l - sr.left) / k, y = (t - sr.top) / k;
  if (x < -4 || y < -4 || x + (r - l) / k > w + 4 || y + (b - t) / k > h + 4) {
    const id = W.slides[shown.slide]?.id || "";
    report(`Slide ${id}: a cue points at something partly outside the visible slide.`, "pointer_offstage", id);
  }
}

// ------------------------------------------------------------------- captions and transcript

function applyCaption(st, slide, force) {
  const seg = TL.segs[st.seg];
  const say = sayOf(seg);
  const key = say ? say.id : "";
  const cap = $("#caption");
  if (force || key !== lastSay) {
    lastSay = key;
    lastWord = -2;
    cap.innerHTML = say && say.text ? `<div class="inner">${captionHtml(say)}</div>` : "";
    markTranscript(key);
  }
  if (!say || !st.inAudio) {
    if (lastWord !== -1) { cap.querySelector(".w.now")?.classList.remove("now"); lastWord = -1; }
    return;
  }
  const i = wordAt(say, st.local);
  if (i === lastWord) return;
  lastWord = i;
  cap.querySelector(".w.now")?.classList.remove("now");
  cap.querySelector(`.w[data-i="${i}"]`)?.classList.add("now");
}

function captionHtml(say) {
  const text = say.text;
  const code = say.code || [];
  const isCode = (a, b) => code.some(([x, y]) => a >= x && b <= y);
  let html = "", pos = 0;
  (say.words || []).forEach((w, i) => {
    if (w.c0 < pos || w.c0 < 0 || w.c1 > text.length) return;
    html += esc(text.slice(pos, w.c0));
    html += `<span class="w${isCode(w.c0, w.c1) ? " code" : ""}" data-i="${i}">${esc(text.slice(w.c0, w.c1))}</span>`;
    pos = w.c1;
  });
  return html + esc(text.slice(pos));
}

function renderTranscript() {
  const box = $("#transcript-body");
  const parts = [];
  W.slides.forEach((s, i) => {
    const paras = s.says.filter((x) => x.text).map((x) => `<p data-say="${esc(x.id)}" tabindex="-1">${esc(x.text)}</p>`).join("");
    parts.push(`<section data-slide="${i}"><h3><button data-slide="${i}">${i + 1}. ${esc(stripMd(s.title))}</button></h3>${paras}</section>`);
  });
  const notes = Object.entries(W.notes || {});
  if (notes.length) parts.push(`<section class="notes"><h3>Sources</h3>${notes.map(([k, v]) => `<p><sup>${esc(k)}</sup> ${linkify(v)}</p>`).join("")}</section>`);
  box.innerHTML = parts.join("");
  markTranscript(lastSay);
  txNav.refresh();
}

// One tab stop per list: the current entry is focusable, the arrow keys move between entries.
function roving(box, selector) {
  const items = () => [...box.querySelectorAll(selector)];
  const current = () => box.querySelector(`${selector}[tabindex="0"]`) || items()[0];
  box.addEventListener("keydown", (e) => {
    const list = items();
    const i = list.indexOf(e.target);
    if (i < 0) return;
    const j = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: list.length - 1 }[e.key];
    if (j === undefined) return;
    e.preventDefault();
    e.stopPropagation();
    const next = list[Math.max(0, Math.min(list.length - 1, j))];
    list.forEach((el) => el.setAttribute("tabindex", el === next ? "0" : "-1"));
    next.focus();
  });
  return { refresh(active) {
    const list = items();
    const keep = active || current();
    list.forEach((el) => el.setAttribute("tabindex", el === keep ? "0" : "-1"));
  } };
}
const txNav = roving($("#transcript-body"), "p[data-say], h3 button");
const chapterNav = $("#chapters") ? roving($("#chapters"), "button") : null;

function linkify(s) {
  return esc(s).replace(/https?:\/\/[^\s<]+/g, (u) => `<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
}

function stripMd(s) { return String(s || "").replace(/[`*_]/g, ""); }

function markTranscript(sayId) {
  const box = $("#transcript-body");
  box.querySelectorAll("p.now").forEach((p) => p.classList.remove("now"));
  if (!sayId) return;
  const p = box.querySelector(`p[data-say="${CSS.escape(sayId)}"]`);
  if (p) {
    p.classList.add("now");
    if (!$("#transcript-body").contains(document.activeElement)) txNav.refresh(p);
    if (!$("#transcript").hidden) p.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }
}

$("#transcript-body").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-slide]");
  if (b) { const t = slideStart(TL, +b.dataset.slide); if (t !== null) seek(t); return; }
  const p = e.target.closest("p[data-say]");
  if (p) { const s = TL.segs.find((x) => x.key === p.dataset.say); if (s) seek(s.start); }
});
$("#transcript-body").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches("p[data-say]")) e.target.click();
});

// ------------------------------------------------------------------- chrome: progress, time, overlay, end

function renderChrome() {
  const bar = $("#progress");
  const spans = slideSpans(TL, W.slides.length);
  bar.replaceChildren(...W.slides.map((s, i) => {
    const sp = spans[i] || { start: 0, end: 0, ready: false };
    const seg = document.createElement("div");
    seg.className = `seg${sp.ready ? "" : " pending"}`;
    seg.style.flex = `${Math.max(0.4, sp.end - sp.start)} 1 0`;
    seg.title = `${i + 1}. ${stripMd(s.title)}`;
    seg.dataset.slide = i;
    seg.innerHTML = '<div class="fill"></div>';
    return seg;
  }));
  bar.setAttribute("aria-valuemax", Math.round(TL.total));
  const writing = W.status !== "finished";
  const voicing = TL.segs.filter((s) => !s.ready).length;
  $("#status").textContent = writing ? `Writing · ${W.slides.length} slides` : voicing ? `Voicing ${voicing}…` : "";
  renderTranscript();
  renderChapters();
}

function renderChapters() {
  const box = $("#chapters");
  if (!box) return;
  box.innerHTML = W.slides.map((s, i) => `<li><button data-slide="${i}" tabindex="-1">${esc(stripMd(s.title))}</button></li>`).join("");
  chapterNav?.refresh(box.querySelector(`button[data-slide="${Math.max(0, shown.slide)}"]`));
}

function markChapter(i) {
  $("#chapters")?.querySelectorAll("button").forEach((b) => b.classList.toggle("now", +b.dataset.slide === i));
  if (!$("#chapters")?.contains(document.activeElement)) chapterNav?.refresh($("#chapters")?.querySelector(`button[data-slide="${i}"]`));
}

function updateTime(st) {
  const t = now();
  const more = W.status !== "finished" ? "+" : "";
  $("#time").textContent = `${mmss(t)} / ${mmss(TL.total)}${more}`;
  const spans = slideSpans(TL, W.slides.length);
  const segs = $("#progress").children;
  for (let k = 0; k < segs.length; k++) {
    const sp = spans[k];
    const f = sp ? Math.max(0, Math.min(1, (t - sp.start) / Math.max(0.01, sp.end - sp.start))) : 0;
    segs[k].firstChild.style.width = `${f * 100}%`;
  }
  const bar = $("#progress");
  bar.setAttribute("aria-valuenow", Math.round(t));
  bar.setAttribute("aria-valuetext", `${mmss(t)} of ${mmss(TL.total)}, slide ${st.slide + 1} of ${W.slides.length}`);
}

function progressSeek(e) {
  const bar = $("#progress");
  const seg = e.target.closest(".seg");
  if (!seg) return;
  const r = seg.getBoundingClientRect();
  const sp = slideSpans(TL, W.slides.length)[+seg.dataset.slide];
  if (!sp) return;
  const f = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  seek(sp.start + f * (sp.end - sp.start));
  bar.focus({ preventScroll: true });
}
$("#progress").addEventListener("pointerdown", (e) => { progressSeek(e); $("#progress").setPointerCapture(e.pointerId); });
$("#progress").addEventListener("pointermove", (e) => { if (e.buttons) progressSeek(e); });

function setPlayIcon() {
  $("#play-icon").setAttribute("d", playing ? "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" : "M8 5v14l11-7z");
  $("#play").setAttribute("aria-label", playing ? "Pause" : "Play");
}

function showOverlay() {
  const o = $("#overlay");
  o.hidden = false;
  const atTitle = (TL.segs[pos.seg]?.slide ?? 0) === 0;
  const titleSlide = W.slides[0]?.kind === "title";
  o.classList.toggle("over-title", titleSlide && atTitle);
  $("#overlay-title").textContent = titleSlide ? "" : stripMd(W.title);
  const ready = TL.segs[0]?.ready;
  $("#big-play").disabled = !ready;
  o.classList.toggle("loading", !ready);
  const sub = titleSlide ? "" : W.slides[0]?.shown || "";
  $("#overlay-sub").textContent = stripMd(sub);
  $("#overlay-meta").textContent = !ready
    ? "Preparing the opening…"
    : W.status === "finished"
      ? `${W.slides.length} slides · ${mmss(TL.total)}`
      : "Still being written. Press play; it keeps up.";
}

function showEnd() {
  const box = $("#questions");
  box.innerHTML = "";
  for (const q of W.questions || []) {
    const b = document.createElement("button");
    b.className = "q";
    b.textContent = q;
    b.onclick = () => navigator.clipboard?.writeText(q).then(() => { b.classList.add("copied"); b.dataset.label = "Copied"; setTimeout(() => b.classList.remove("copied"), 1500); }).catch(() => {});
    box.append(b);
  }
  $("#end-questions").hidden = !(W.questions || []).length;
  $("#end").hidden = false;
  $("#replay").focus({ preventScroll: true });
}

function announce(text) { $("#announce").textContent = text; }

// ------------------------------------------------------------------- presenting

// Present without the voice: each press moves to the next step (a slide, or the next part of a
// step-reveal slide). The narration becomes speaker notes. An audience window follows along.
let channel = null, stepIdx = 0, presentStarted = 0;

function startPresenting() {
  body.classList.add("presenter");
  $("#notes").hidden = false;
  setTranscript(false);
  $("#overlay").hidden = true;
  started = true;
  channel = "BroadcastChannel" in window ? new BroadcastChannel(`showntell:${W.id}`) : null;
  $("#notes-audience").onclick = () => {
    const u = new URL(location.href);
    u.searchParams.delete("present");
    u.searchParams.set("audience", "1");
    window.open(u.toString(), `showntell-audience-${W.id}`, "popup");
  };
  channel?.addEventListener("message", (e) => { if (e.data?.hello) channel.postMessage({ t: now() }); });
  setInterval(() => { if (presentStarted) $("#notes-clock").textContent = mmss((performance.now() - presentStarted) / 1000); }, 500);
  presenterSync();
}

function stepBy(d) {
  const all = steps(W, TL);
  if (!all.length) return;
  const t = now();
  // The step we are on: the last one at or before the current time.
  let i = 0;
  for (let k = 0; k < all.length; k++) if (all[k].t <= t + 0.01) i = k;
  i = Math.max(0, Math.min(all.length - 1, i + d));
  if (!presentStarted) presentStarted = performance.now();
  seek(all[i].t);
}

function presenterSync() {
  if (!PRESENT || !W) return;
  const all = steps(W, TL);
  const t = now();
  let i = 0;
  for (let k = 0; k < all.length; k++) if (all[k].t <= t + 0.01) i = k;
  stepIdx = i;
  const st = stateAt(W, TL, t);
  const slide = W.slides[st.slide];
  if (!slide) return;
  const paras = slide.says.filter((x) => x.text);
  const current = all[i]?.say ?? -1;
  $("#notes-step").textContent = `Slide ${st.slide + 1} of ${W.slides.length} · step ${i + 1} of ${all.length}`;
  $("#notes-body").innerHTML = paras.map((x) => `<p class="${slide.says.indexOf(x) === current ? "now" : ""}">${esc(x.text)}</p>`).join("");
  const next = W.slides[st.slide + 1];
  $("#notes-next").textContent = next ? `Next: ${stripMd(next.title)}` : "Last slide";
  channel?.postMessage({ t });
}

function followPresenter() {
  body.classList.add("audience");
  $("#overlay").hidden = true;
  started = true;
  const ch = "BroadcastChannel" in window ? new BroadcastChannel(`showntell:${W.id}`) : null;
  ch?.addEventListener("message", (e) => { if (Number.isFinite(e.data?.t)) seek(e.data.t); });
  ch?.postMessage({ hello: true });
}

// ------------------------------------------------------------------- controls

$("#big-play").onclick = play;
$("#play").onclick = toggle;
$("#prev").onclick = () => seekSlide(-1);
$("#next").onclick = () => seekSlide(1);
$("#replay").onclick = () => { $("#end").hidden = true; ended = false; seek(0); play(); };
$("#end-transcript").onclick = () => setTranscript(true);
$("#speed").value = String(rate);
$("#speed").onchange = (e) => { rate = parseFloat(e.target.value) || 1; audio.playbackRate = rate; store.set("rate", rate); };
$("#pause-after").checked = pauseAfterSlide;
$("#pause-after").onchange = (e) => { pauseAfterSlide = e.target.checked; store.set("pauseAfterSlide", pauseAfterSlide); };
$("#theme").value = store.get("theme", "") || "";
$("#theme").onchange = (e) => { store.set("theme", e.target.value || null); location.reload(); };
$("#pause-steps").checked = pauseSteps;
$("#pause-steps").onchange = (e) => { pauseSteps = e.target.checked; store.set("pauseSteps", pauseSteps); };
$("#present").onclick = () => { const u = new URL(location.href); u.searchParams.set("present", "1"); location.href = u.toString(); };
$("#ask").onclick = () => {
  // A ready-made request for the agent: which slide, where, and what it currently says.
  const seg = currentSeg();
  if (!W || !seg) return;
  const slide = W.slides[seg.slide];
  const said = (sayOf(seg)?.text || "").slice(0, 160);
  const text = `In the showntell walkthrough "${stripMd(W.title)}" (id ${W.id}), revise slide "${slide.id}" (${stripMd(slide.title)}), `
    + `at ${mmss(now())}${said ? `, where the narration says: "${said}${said.length >= 160 ? "…" : ""}"` : ""}. Change: `;
  navigator.clipboard?.writeText(text).then(() => {
    $("#ask").classList.add("done");
    $("#ask").textContent = "Copied. Paste it to your agent";
    setTimeout(() => { $("#ask").classList.remove("done"); $("#ask").textContent = "Copy a change request for this slide"; }, 2500);
  }).catch(() => {});
};
$("#chapters")?.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-slide]");
  if (b) { const t = slideStart(TL, +b.dataset.slide); if (t !== null) seek(t); $("#menu").hidden = true; }
});

function setCaptions(on) {
  captionsOn = on;
  $("#caption").classList.toggle("off", !on);
  $("#cc").setAttribute("aria-pressed", on);
  store.set("cc", on);
}
$("#cc").onclick = () => setCaptions(!captionsOn);
setCaptions(captionsOn);

function setTranscript(on) {
  $("#transcript").hidden = !on;
  $("#tx").setAttribute("aria-pressed", on);
  body.classList.toggle("with-transcript", on);
  store.set("transcript", on);
  if (on) markTranscript(lastSay);
}
$("#tx").onclick = () => setTranscript($("#transcript").hidden);
$("#transcript-close").onclick = () => setTranscript(false);
setTranscript(store.get("transcript", false) && innerWidth > 900);

$("#more").onclick = () => { const m = $("#menu"); m.hidden = !m.hidden; $("#more").setAttribute("aria-expanded", !m.hidden); };
document.addEventListener("click", (e) => { if (!e.target.closest("#menu, #more")) { $("#menu").hidden = true; $("#more").setAttribute("aria-expanded", "false"); } });

document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = e.target.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
  const onButton = tag === "BUTTON" && (e.key === " " || e.key === "Enter");
  if (onButton) return;
  const k = e.key;
  if (PRESENT && [" ", "ArrowRight", "PageDown", "n", "ArrowDown"].includes(k)) { e.preventDefault(); return stepBy(1); }
  if (PRESENT && ["ArrowLeft", "PageUp", "p", "ArrowUp", "Backspace"].includes(k)) { e.preventDefault(); return stepBy(-1); }
  if (k === " " || k === "k") { e.preventDefault(); toggle(); }
  else if (k === "ArrowRight" && e.shiftKey) seekSlide(1);
  else if (k === "ArrowLeft" && e.shiftKey) seekSlide(-1);
  else if (k === "ArrowRight" || k === "l") { e.preventDefault(); seek(now() + 5); }
  else if (k === "ArrowLeft" || k === "j") { e.preventDefault(); seek(now() - 5); }
  else if (k === "PageDown" || k === ".") seekSlide(1);
  else if (k === "PageUp" || k === ",") seekSlide(-1);
  else if (k === "Home") seek(0);
  else if (k === "c") setCaptions(!captionsOn);
  else if (k === "t") setTranscript($("#transcript").hidden);
  else if (k === "?") { $("#menu").hidden = false; }

  else if (k === "Escape") { $("#menu").hidden = true; }
});

audio.addEventListener("playing", () => {
  // Wall-clock start of each paragraph's audio, so recordings can line sound up with pictures.
  const seg = currentSeg();
  if (seg) metrics.starts.push({ key: seg.key, at: performance.timeOrigin + performance.now(), local: audio.currentTime });
  if (playPressed && metrics.startDelay === null) {
    metrics.startDelay = (performance.now() - playPressed) / 1000;
    if (metrics.startDelay > 2) report(`The first narration took ${metrics.startDelay.toFixed(1)} s to start after play was pressed.`, "viewer_waited");
  }
});
audio.addEventListener("error", () => {
  // A missing or broken file: carry on silently with captions rather than stopping.
  if (!audio.src) return;
  const name = audio.src.split("/").pop();
  const say = sayOf(currentSeg());
  if (say?.audio) failedAudio.add(say.audio);
  seekingAudio = false;
  audio.removeAttribute("src");
  report(`Audio failed to load: ${name}`, "audio_error", W.slides[currentSeg()?.slide]?.id || "");
});

load().catch((e) => {
  $("#overlay-meta").textContent = "This walkthrough could not be loaded.";
  report(`Load failed: ${e.message}`);
  emit("error", { message: e.message });
});

// ------------------------------------------------------------------- capture mode (?capture=1)
// Frame-by-frame capture (capture.py: MP4 export, posters, thumbnails) drives the player by
// seeking, never by playing: no audio, no wall-clock animation (player.css turns transitions off
// under body.capture), only the stage on screen. ?captions=1 burns the caption into the frame.
// seek(t) resolves once the frame for t is painted, with a key that changes whenever the picture
// can change, so the caller can reuse the previous frame while the key repeats.
if (params.get("capture") === "1") {
  body.classList.add("capture");
  const burn = params.get("captions") === "1";
  if (burn) body.classList.add("burn-captions");
  const painted = () => new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
  const stateKey = () => [shown.id, shown.rev, view?.done, lastReveal, lastCueKey, burn ? `${lastSay}:${lastWord}` : ""].join("|");
  let paintedKey = "";
  window.__showntell = Object.assign(window.__showntell || {}, {
    duration: () => (W && TL.segs.length ? TL.total : 0),
    frame: () => (W ? W.frame : ""),
    async seek(t) {
      seek(t);
      // Nothing changed since the last painted frame: skip the wait (most frames of a video).
      if (view?.done && stateKey() === paintedKey) return paintedKey;
      // Mermaid, KaTeX, images and fonts settle in view.ready; showSlide sets view.done after them.
      for (let i = 0; i < 600 && !view?.done; i++) await painted();
      await document.fonts?.ready;
      await painted();
      return (paintedKey = stateKey());
    },
  });
}
