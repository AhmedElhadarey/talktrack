// The showntell player. One clock drives everything: the audio of the current paragraph while
// it plays, wall time during pauses between paragraphs. Each frame computes the state for the
// current time (timeline.js) and brings the screen in line with it.

import { build, mmss, segmentAt, slideSpans, slideStart, stateAt, wordAt } from "./timeline.js";
import { diagramEdges, diagramNodes, fit, renderSlide, sizeDiagram, textRange } from "./render.js";
import { esc, norm } from "./util.js";

const $ = (s) => document.querySelector(s);
const body = document.body;
const LIVE = body.dataset.live === "1";
const REPORT_KEY = body.dataset.reportKey || "";
const params = new URLSearchParams(location.search);
const FRAMES = { landscape: [1280, 720], square: [960, 960], portrait: [720, 1280] };

const stage = $("#stage"), host = $("#slide-host"), pointer = $("#pointer");
const audio = new Audio();
audio.preload = "auto";
const prefetch = new Audio();
prefetch.preload = "auto";

// Read by the evaluation harness and the video recorder.
const metrics = { stalls: 0, stallSeconds: 0, renderErrors: [], ended: false, starts: [] };
window.__showntell = metrics;

let W = null; // walkthrough data
let TL = { segs: [], total: 0 };
let pos = { seg: 0, local: 0 };
let playing = false, started = false, waitingSince = 0, lastFrame = 0, ended = false;
let shown = { slide: -1, rev: -1, id: "" }; // what is on screen
let view = null; // {parts, ready, root, body}
let lastCueKey = "", lastReveal = "", lastSay = "", lastWord = -2;
let rate = 1, captionsOn = true, pauseAfterSlide = false;
let seekingAudio = false;
const failedAudio = new Set(); // audio URLs that failed to load; played silently instead

const store = {
  get(k, d) { try { const v = localStorage.getItem(`showntell.${k}`); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`showntell.${k}`, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};
rate = store.get("rate", 1);
captionsOn = store.get("cc", true);
pauseAfterSlide = store.get("pauseAfterSlide", false);

// ------------------------------------------------------------------- reporting

const reported = new Set();
function report(message) {
  metrics.renderErrors.push(message);
  if (!LIVE || reported.has(message)) return;
  reported.add(message);
  fetch("report", { method: "POST", headers: { "Content-Type": "application/json", "X-Report-Key": REPORT_KEY }, body: JSON.stringify({ message }) }).catch(() => {});
}
window.addEventListener("error", (e) => report(`Player error: ${e.message}`));

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
  if (params.get("autoplay") === "1") play();
  const t = parseFloat(params.get("t"));
  if (t > 0) seek(t);
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

function frame(ts) {
  requestAnimationFrame(frame);
  const dt = lastFrame ? Math.min(0.25, (ts - lastFrame) / 1000) : 0;
  lastFrame = ts;
  if (!W || !playing) return;
  advance(dt);
  render(false);
}
requestAnimationFrame(frame);

function advance(dt) {
  const seg = currentSeg();
  if (!seg) return stall(W.status === "finished" ? "end" : "writing");
  if (!seg.ready) {
    audio.pause();
    return stall("voicing");
  }
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
  if (seg.error && say) report(`Narration on slide ${W.slides[seg.slide].id} could not be voiced: ${say.error || "unknown error"}`);
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

function prefetchNext() {
  for (let i = pos.seg + 1; i < Math.min(TL.segs.length, pos.seg + 3); i++) {
    const s = sayOf(TL.segs[i]);
    if (s?.audio) { if (!prefetch.src.endsWith(s.audio)) prefetch.src = s.audio; return; }
  }
}

function onBlocked() {
  // Autoplay blocked: wait for a click.
  playing = false;
  setPlayIcon();
  showOverlay();
}

function stall(why) {
  if (why === "end") return finish();
  if (!waitingSince) {
    waitingSince = performance.now();
    if (started) metrics.stalls += 1;
  }
  $("#waiting").hidden = false;
  $("#waiting-text").textContent = why === "voicing" ? "Voicing the next part…" : "Writing the next slide…";
}

function unstall() {
  if (!waitingSince) return;
  metrics.stallSeconds += (performance.now() - waitingSince) / 1000;
  waitingSince = 0;
  $("#waiting").hidden = true;
}

function finish() {
  if (ended) return;
  ended = true;
  metrics.ended = true;
  pause();
  unstall();
  showEnd();
}

function play() {
  if (!W || !TL.segs.length) return;
  if (!TL.segs[0].ready && !started) return;
  if (ended) { ended = false; pos = { seg: 0, local: 0 }; $("#end").hidden = true; }
  started = true;
  playing = true;
  $("#overlay").hidden = true;
  setPlayIcon();
  lastFrame = 0;
  render(true);
}

function pause() {
  playing = false;
  audio.pause();
  setPlayIcon();
}

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
  v.ready.then(() => {
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
  });
  announce(`Slide ${i + 1} of ${W.slides.length}: ${slide.title}`);
  markChapter(i);
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
    if (cue.kind === "item") return { rects: [] }; // the card's own highlight is the pointer
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
    const r = p.visual && findText(p.visual, cue.text);
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
    const paras = s.says.filter((x) => x.text).map((x) => `<p data-say="${esc(x.id)}" tabindex="0">${esc(x.text)}</p>`).join("");
    parts.push(`<section data-slide="${i}"><h3><button data-slide="${i}">${i + 1}. ${esc(stripMd(s.title))}</button></h3>${paras}</section>`);
  });
  const notes = Object.entries(W.notes || {});
  if (notes.length) parts.push(`<section class="notes"><h3>Sources</h3>${notes.map(([k, v]) => `<p><sup>${esc(k)}</sup> ${linkify(v)}</p>`).join("")}</section>`);
  box.innerHTML = parts.join("");
  markTranscript(lastSay);
}

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
  box.innerHTML = W.slides.map((s, i) => `<li><button data-slide="${i}">${esc(stripMd(s.title))}</button></li>`).join("");
}

function markChapter(i) {
  $("#chapters")?.querySelectorAll("button").forEach((b) => b.classList.toggle("now", +b.dataset.slide === i));
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
});
audio.addEventListener("error", () => {
  // A missing or broken file: carry on silently with captions rather than stopping.
  if (!audio.src) return;
  const name = audio.src.split("/").pop();
  const say = sayOf(currentSeg());
  if (say?.audio) failedAudio.add(say.audio);
  seekingAudio = false;
  audio.removeAttribute("src");
  report(`Audio failed to load: ${name}`);
});

load().catch((e) => {
  $("#overlay-meta").textContent = "This walkthrough could not be loaded.";
  report(`Load failed: ${e.message}`);
});
