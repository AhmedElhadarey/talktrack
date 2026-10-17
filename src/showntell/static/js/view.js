// Bring the screen in line with the state for the current time (timeline.js): the slide, its
// step reveal and code version, the pointer, per-frame animation, the caption and the clock.

import { stateAt } from "./timeline.js";
import { fit, READABLE_PX } from "./draw.js";
import { kindOf, renderSlide } from "./render.js";
import { $, emit, FRAMES, host, LIVE, now, pointer, reducedMotion, report, S, scale, stage } from "./state.js";
import { camera, cueTarget, placePointer, scrollCode } from "./pointer.js";
import { announce, applyCaption, markChapter, updateTime } from "./chrome.js";

export function layoutStage() {
  if (!S.W) return;
  const [w, h] = FRAMES[S.W.frame] || FRAMES.landscape;
  const vp = $("#viewport").getBoundingClientRect();
  const pad = vp.width < 640 ? 6 : 16;
  const k = Math.max(0.1, Math.min((vp.width - pad * 2) / w, (vp.height - pad * 2) / h));
  stage.style.width = `${w}px`;
  stage.style.height = `${h}px`;
  stage.style.transform = `scale(${k})`;
  stage.style.left = `${(vp.width - w * k) / 2}px`;
  stage.style.top = `${(vp.height - h * k) / 2}px`;
  stage.dataset.scale = k;
  S.lastCueKey = ""; // re-place the pointer at the new scale
}

export function render(force) {
  const { W, TL, shown } = S;
  if (!W || !TL.segs.length) return;
  const st = stateAt(W, TL, now());
  if (st.slide < 0) return;
  const slide = W.slides[st.slide];
  if (force || shown.slide !== st.slide || shown.rev !== slide.rev || shown.id !== slide.id) {
    if (shown.slide !== st.slide || shown.rev !== slide.rev || shown.id !== slide.id) showSlide(st.slide);
  }
  applyVisual(st, slide, force);
  liveFrame(st, slide);
  applyCaption(st, slide, force);
  updateTime(st);
}

function showSlide(i) {
  const { W } = S;
  const slide = W.slides[i];
  S.shown = { slide: i, rev: slide.rev, id: slide.id };
  S.lastCueKey = S.lastReveal = S.zoomFor = "";
  clearMarks();
  const v = renderSlide(slide, { frame: W.frame, report });
  host.replaceChildren(v.root);
  // The slide eases in from a few pixels below; measure the pointer again once it has landed.
  v.root.addEventListener("animationend", () => { if (S.view?.root === v.root) { S.lastCueKey = ""; render(true); } }, { once: true });
  const bodyEl = v.root.querySelector(".slide-body");
  const view = S.view = { ...v, body: bodyEl, done: false };
  const kind = kindOf(slide);
  v.ready.then(() => document.fonts?.ready).then(() => {
    if (view.root !== S.view.root) return;
    if (bodyEl) (kind.layout || fit)(bodyEl, view.parts);
    view.done = true;
    S.lastCueKey = S.lastReveal = "";
    render(true);
    const idle = window.requestIdleCallback || ((f) => setTimeout(f, 50));
    idle(() => { if (S.view?.root === v.root) { measure(slide, bodyEl, view.parts); view.measured = true; } });
  });
  announce(`Slide ${i + 1} of ${W.slides.length}: ${slide.title}`);
  markChapter(i);
  emit("slidechange", { index: i, id: slide.id, title: slide.title });
}

// Report what the layout did to the slide: text shrunk too far to read, or content that still
// does not fit. Measured once a slide has settled, in stage pixels.
function measure(slide, bodyEl, parts) {
  if (!bodyEl || !LIVE) return;
  const content = bodyEl.firstElementChild;
  if (!content) return;
  const id = slide.id;
  if (kindOf(slide).measure?.(slide, bodyEl, parts, report)) return;
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
  const { view } = S;
  if (!view?.done) return;
  if (view.parts.setStep && view.parts.step !== st.step) {
    // Code that evolves: show the version for this moment, gliding only during playback.
    view.parts.setStep(st.step, S.playing && !reducedMotion());
    force = true;
  }
  const revealKey = JSON.stringify(st.reveal);
  if (force || revealKey !== S.lastReveal) {
    S.lastReveal = revealKey;
    applyReveal(st.reveal, slide);
  }
  const cueKey = st.cue ? `${st.cue._say}:${st.cue.at}:${st.cue.kind}:${st.cue.id ?? ""}:${st.cue.l0 ?? ""}` : "none";
  if (!force && cueKey === S.lastCueKey) return;
  S.lastCueKey = cueKey;
  if (S.playing && st.cue && cueKey !== S.lastAnnounced) describeCue(st.cue, slide, cueKey);
  host.querySelectorAll(".active").forEach((e) => e.classList.remove("active"));
  host.querySelectorAll(".has-active").forEach((e) => e.classList.remove("has-active"));
  host.querySelectorAll(".ln.hl").forEach((e) => e.classList.remove("hl"));
  if (window.CSS?.highlights) CSS.highlights.delete("ptr");
  if (scrollCode(st, cueKey)) return; // the pointer is placed once the code has scrolled
  if (!st.cue) { pointer.hidden = true; camera(null, cueKey); return; }
  const target = cueTarget(st.cue, slide);
  if (!target) { pointer.hidden = true; camera(null, cueKey); return; }
  if (camera((st.cue.style || "").includes("zoom") ? target.rects : null, cueKey)) return;
  // The camera may have just jumped: measure the target where it is now.
  const here = cueTarget(st.cue, slide) || target;
  placePointer(here.rects, here.wide, st.cue.style);
}

// Tell screen readers what only moves on screen: the route a travelling dot takes, and what the
// camera zooms onto. Other cues name their target in the narration itself.
function describeCue(cue, slide, cueKey) {
  const nodes = slide.visual?.nodes || {};
  const text = cue.kind === "edge" ? `Path: ${cue.path.map((id) => nodes[id] || id).join(" to ")}`
    : (cue.style || "").includes("zoom") ? `Zoomed in on ${cue.label}` : "";
  if (!text) return;
  S.lastAnnounced = cueKey;
  announce(text);
}

// Step reveals: items, rows and lines here; anything else (flowchart nodes) by the slide's kind.
function applyReveal(reveal, slide) {
  const p = S.view.parts;
  const veil = (el, on) => el && el.classList.toggle("veiled", on);
  if (!reveal) {
    host.querySelectorAll(".veiled").forEach((e) => e.classList.remove("veiled"));
    p.items.forEach((e) => e.classList.remove("veiled")); // a drawing's parts sit in a shadow root, out of the query's reach
    return;
  }
  if ("items" in reveal) p.items.forEach((el) => veil(el, +el.dataset.idx >= reveal.items));
  if ("rows" in reveal) p.rows.forEach((el) => veil(el, +el.dataset.idx >= reveal.rows));
  if ("line" in reveal) p.lines.new.forEach((el, n) => veil(el, n > reveal.line));
  kindOf(slide).reveal?.(reveal, p, veil);
}

// Every frame: whatever the slide's kind animates with time (a dot travelling along diagram
// edges, numbers counting, checklist ticks).
function liveFrame(st, slide) {
  if (!S.view?.done) return;
  kindOf(slide).frame?.(st, slide, S.view.parts, now());
}
