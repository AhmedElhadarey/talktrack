// Bring the screen in line with the state for the current time (timeline.js): the slide, its
// step reveal and code version, the pointer, per-frame animation, the caption and the clock.

import { stateAt } from "./timeline.js";
import { fit, READABLE_PX } from "./draw.js";
import { kindOf, layoutOf, renderSlide } from "./render.js";
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
    if (shown.slide !== st.slide || shown.rev !== slide.rev || shown.id !== slide.id) showSlide(st.slide, st.sinceSlide);
  }
  const entering = applyEntrance(st);
  if (entering && st.cue) S.lastCueKey = ""; // the slide is still moving in: place the pointer where the target is now
  applyVisual(st, slide, force);
  liveFrame(st, slide, entering);
  applyCaption(st, slide, force);
  updateTime(st);
}

function showSlide(i, since = Infinity) {
  const { W } = S;
  const slide = W.slides[i];
  const was = S.shown.slide;
  S.shown = { slide: i, rev: slide.rev, id: slide.id };
  S.lastCueKey = S.lastReveal = S.zoomFor = "";
  clearMarks();
  const v = renderSlide(slide, { frame: W.frame, report, prev: W.slides[i - 1], ...placeOf(W, i) });
  // Moving on by one slide, the old one stays for the transition; any other jump cuts, unless it
  // lands inside the transition (a seek, or an export worker starting its share of the video):
  // then the slide before is drawn again, as it ended, so the frame matches playing through.
  let old = was === i - 1 ? host.querySelector(".slide:not(.leaving)") : null;
  const redrawn = !old && i > 0 && since < ENTER_S && !reducedMotion()
    ? renderSlide(W.slides[i - 1], { frame: W.frame, report, prev: W.slides[i - 2], ...placeOf(W, i - 1) }) : null;
  if (redrawn) old = redrawn.root;
  host.querySelectorAll(".slide").forEach((e) => { if (e !== old) e.remove(); });
  if (old) old.classList.add("leaving");
  if (redrawn) host.append(old);
  host.append(v.root);
  S.enter = { type: entranceFor(W, i, slide), root: v.root, old, done: false };
  const bodyEl = v.root.querySelector(".slide-body");
  const view = S.view = { ...v, body: bodyEl, done: false };
  const panes = v.panes || [];
  Promise.all([v.ready, redrawn?.ready]).then(() => document.fonts?.ready).then(() => {
    if (view.root !== S.view.root) return;
    for (const p of panes) (p.kind.layout || fit)(p.body, p.parts);
    if (redrawn) {
      const end = stateAt(W, S.TL, now() - since - 0.05);
      (redrawn.panes || []).forEach((p, k, all) => {
        (p.kind.layout || fit)(p.body, p.parts);
        p.kind.frame?.(all.length > 1 ? paneState(end, k) : end, p.slide, p.parts, now() - since - 0.05);
      });
    }
    view.done = true;
    S.lastCueKey = S.lastReveal = "";
    render(true);
    const idle = window.requestIdleCallback || ((f) => setTimeout(f, 50));
    idle(() => {
      if (S.view?.root !== v.root) return;
      for (const p of panes) measure(p.slide, p.body, p.parts);
      view.measured = true;
    });
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

// The slide's number among the content slides, and the part it belongs to, for its kicker.
function placeOf(W, i) {
  let n = 0, chapter = "";
  for (let k = 0; k <= i; k++) {
    const s = W.slides[k];
    if (s.kind === "chapter") chapter = s.title;
    else if (s.kind !== "title") n++;
  }
  return { n: W.slides[i].kind === "title" ? 0 : n, chapter };
}

// How a slide comes in. Driven by the clock (seconds since the slide began), so seeking and frame
// capture land on the same in-between picture, and MP4 export shows the same motion.
const ENTER_S = 0.6;
const STAGGER_S = 0.07, ITEM_S = 0.4;
const easeOut = (p) => 1 - (1 - p) ** 3;
const clamp = (x) => Math.max(0, Math.min(1, x));

function entranceFor(W, i, slide) {
  if (reducedMotion()) return "none";
  const prev = W.slides[i - 1];
  if (slide.kind === "title" || slide.kind === "chapter") return "zoom";
  if (slide.kind === "svg" && prev?.kind === "svg" && (slide.classes || []).includes("morph")) return "morph";
  // Slides of the same kind and layout in a row slide across, like turning a page.
  if (prev && prev.kind === slide.kind && layoutOf(prev) === layoutOf(slide) && slide.kind !== "text") return "push";
  return "rise";
}

function applyEntrance(st) {
  const e = S.enter;
  if (!e || e.done || e.root !== S.view?.root) return "";
  const t = st.sinceSlide;
  const p = e.type === "none" ? 1 : clamp(t / ENTER_S), k = easeOut(p);
  const r = e.root.style, o = e.old?.style;
  if (e.type === "push") {
    r.transform = `translateX(${((1 - k) * 100).toFixed(2)}%)`;
    if (o) o.transform = `translateX(${(-k * 100).toFixed(2)}%)`;
  } else if (e.type === "zoom") {
    r.opacity = k.toFixed(3);
    r.transform = `scale(${(1.04 - 0.04 * k).toFixed(4)})`;
    if (o) o.opacity = (1 - clamp(p * 2)).toFixed(3);
  } else if (e.type === "morph") {
    const h = e.root.querySelector(".slide-head");
    if (h) h.style.opacity = k.toFixed(3);
    if (o) o.opacity = "0";
  } else if (e.type === "rise") {
    r.opacity = k.toFixed(3);
    r.transform = `translateY(${((1 - k) * 18).toFixed(2)}px)`;
    if (o) o.opacity = (1 - clamp(p * 2.5)).toFixed(3);
  }
  // Cards, numbers, rows, messages and takeaways arrive one after another.
  const items = e.type === "none" || e.root.classList.contains("build") ? [] : staggered(e.root);
  let last = 0;
  items.forEach((el, i) => {
    const q = easeOut(clamp((t - 0.12 - i * STAGGER_S) / ITEM_S));
    last = Math.max(last, (0.12 + i * STAGGER_S + ITEM_S) / ENTER_S);
    el.classList.toggle("entering", q < 1);
    el.style.setProperty("--in", q.toFixed(3));
  });
  if (p >= 1 && t / ENTER_S >= last) {
    r.transform = r.opacity = "";
    e.root.querySelector(".slide-head")?.style.removeProperty("opacity");
    items.forEach((el) => { el.classList.remove("entering"); el.style.removeProperty("--in"); });
    e.old?.remove();
    e.done = true;
    S.lastCueKey = ""; // landed: measure the pointer again
    return "";
  }
  return `${e.type}:${Math.round(t * 30)}`;
}

function staggered(root) {
  if (!root._stagger) {
    root._stagger = [...root.querySelectorAll(".list > .entry, .stats > .stat, .chat > .msg, table.data tbody tr, .points > .point")]
      .filter((x) => !x.closest(".list.timeline"));
  }
  return root._stagger;
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
// edges, numbers counting, checklist ticks, a drawing changing version). The kind returns a key
// for what it drew, so frame capture knows when the picture moved.
function liveFrame(st, slide, entering) {
  if (!S.view?.done) { S.frameKey = entering; return; }
  const panes = S.view.panes || [];
  const keys = panes.map((p, i) => p.kind.frame?.(panes.length > 1 ? paneState(st, i) : st, p.slide, p.parts, now()) || "");
  S.frameKey = `${keys.join("/")}${entering ? `|${entering}` : ""}`;
}

// The state one pane of a `.pair` slide sees: only the cues that point into it.
function paneState(st, i) {
  const cues = st.cues.filter((c) => (c.pane || 0) === i);
  return { ...st, cues, cue: cues.length ? cues[cues.length - 1] : null };
}
