"use strict";
// Player (to be replaced by the time-driven renderer).

const ID = document.body.dataset.id;
const $ = (s) => document.querySelector(s);
const stage = $("#stage");
const host = $("#slide-host");
const pointer = $("#pointer");
const audio = new Audio();
audio.preload = "auto";
const prefetch = new Audio();
prefetch.preload = "auto";

const FRAMES = { landscape: [1280, 720], square: [960, 960], portrait: [720, 1280] };
const GAP_MS = 220; // pause between says
const WORDS_PER_S = 2.6;

let S = null; // latest snapshot
let cur = 0; // say index being played
let playing = false;
let started = false;
let waiting = false;
let shownSlide = -1;
let slideReady = Promise.resolve();
let anchorEls = {}; // anchor -> element on the current slide
let fired = new Set();
let speed = 1;
let captionsOn = true;
let raf = 0;
let advanceTimer = 0;
const reported = new Set();

try {
  speed = parseFloat(localStorage.getItem("explain.speed")) || 1;
  captionsOn = localStorage.getItem("explain.cc") !== "0";
} catch (e) { /* storage unavailable */ }

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "base",
  fontFamily: "ui-sans-serif, system-ui, -apple-system, sans-serif",
  themeVariables: {
    darkMode: true,
    background: "#12161e",
    fontSize: "20px",
    primaryColor: "#1d2533",
    primaryBorderColor: "#7cc4ff",
    primaryTextColor: "#e9edf3",
    secondaryColor: "#222a37",
    tertiaryColor: "#1a202b",
    lineColor: "#95a0b2",
    textColor: "#e9edf3",
    mainBkg: "#1d2533",
    nodeBorder: "#7cc4ff",
    clusterBkg: "#161b24",
    clusterBorder: "#2c3544",
    edgeLabelBackground: "#12161e",
    actorBkg: "#1d2533",
    actorBorder: "#7cc4ff",
    actorTextColor: "#e9edf3",
    signalColor: "#c9d1dc",
    signalTextColor: "#e9edf3",
    labelBoxBkgColor: "#1d2533",
    labelTextColor: "#e9edf3",
    loopTextColor: "#e9edf3",
    noteBkgColor: "#2b2a1f",
    noteTextColor: "#f3e9c6",
    noteBorderColor: "#ffc857",
  },
  flowchart: { htmlLabels: true, curve: "basis", padding: 14 },
  sequence: { actorMargin: 60, messageFontSize: 18, actorFontSize: 18, noteFontSize: 16 },
});

// ---------------------------------------------------------------- helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code class="inline">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function mmss(s) {
  s = Math.max(0, Math.round(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function report(message) {
  if (reported.has(message)) return;
  reported.add(message);
  fetch(`/api/e/${ID}/report`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) }).catch(() => {});
}
function sayDuration(say) {
  return say.duration || say.plain.split(/\s+/).length / WORDS_PER_S;
}

// ---------------------------------------------------------------- layout

function layoutStage() {
  const [w, h] = FRAMES[S?.mode || "landscape"];
  const vp = $("#viewport").getBoundingClientRect();
  const pad = 16;
  const scale = Math.min((vp.width - pad * 2) / w, (vp.height - pad * 2) / h);
  stage.style.transform = `scale(${scale})`;
  stage.style.left = `${(vp.width - w * scale) / 2}px`;
  stage.style.top = `${(vp.height - h * scale) / 2}px`;
  stage.dataset.scale = scale;
  if (lastTarget) placePointer(lastTarget, lastWide);
}
window.addEventListener("resize", layoutStage);

function fit(body) {
  const content = body.firstElementChild;
  if (!content || content.classList.contains("diagram") || content.classList.contains("image")) return;
  content.style.zoom = 1;
  const bw = body.clientWidth, bh = body.clientHeight;
  const cw = content.scrollWidth, ch = content.scrollHeight;
  const grow = content.matches(".codebox, .diff, .math, table.data") ? 1.35 : 1;
  const z = Math.min(grow, bw / cw, bh / ch) * 0.98;
  if (Math.abs(z - 1) > 0.02) content.style.zoom = Math.max(0.4, z);
}

// ---------------------------------------------------------------- code rendering

function highlight(code, lang) {
  try {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    return hljs.highlightAuto(code).value;
  } catch (e) {
    return esc(code);
  }
}

function splitLines(html) {
  // Split highlighted HTML into lines, closing and reopening spans that cross line breaks.
  const lines = [];
  const stack = [];
  let curLine = "";
  const re = /(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+|<)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) { stack.push(m[1]); curLine += m[1]; }
    else if (m[2]) { stack.pop(); curLine += m[2]; }
    else if (m[3]) { curLine += "</span>".repeat(stack.length); lines.push(curLine); curLine = stack.join(""); }
    else curLine += m[4];
  }
  lines.push(curLine);
  return lines;
}

function codeBox(code, lang, label, tag) {
  const box = el("div", "codebox");
  if (label || tag) {
    box.append(el("div", "file pane-label", `<span>${esc(label || "")}</span>${tag ? `<span class="tag">${esc(tag)}</span>` : ""}`));
  }
  const body = el("div", "code hljs");
  body.style.background = "transparent";
  splitLines(highlight(code, lang)).forEach((line, i) => {
    const ln = el("div", "ln", `<span class="no">${i + 1}</span><span class="tx">${line || " "}</span>`);
    body.append(ln);
  });
  box.append(body);
  return box;
}

function lineEls(box) {
  return [...box.querySelectorAll(".ln")];
}

function textRange(container, start, end) {
  // Build a DOM Range covering [start, end) characters of container's text.
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let pos = 0, node, startSet = false;
  while ((node = walker.nextNode())) {
    const len = node.nodeValue.length;
    if (!startSet && start < pos + len) { range.setStart(node, start - pos); startSet = true; }
    if (startSet && end <= pos + len) { range.setEnd(node, end - pos); return range; }
    pos += len;
  }
  return startSet ? range : null;
}

// ---------------------------------------------------------------- diff

function lcs(a, b, eq) {
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = eq(a[i], b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const pairs = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (eq(a[i], b[j])) { pairs.push([i, j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return pairs;
}

function markDiff(beforeBox, afterBox, before, after) {
  const a = before.split("\n"), b = after.split("\n");
  const pairs = lcs(a, b, (x, y) => x.trim() === y.trim());
  const keptA = new Set(pairs.map((p) => p[0])), keptB = new Set(pairs.map((p) => p[1]));
  const la = lineEls(beforeBox), lb = lineEls(afterBox);
  a.forEach((_, i) => { if (!keptA.has(i)) la[i]?.classList.add("del"); });
  b.forEach((_, j) => { if (!keptB.has(j)) lb[j]?.classList.add("add"); });
  // Word-level changes for lines replaced one-for-one between unchanged anchors.
  const delRanges = [], addRanges = [];
  const bounds = [[-1, -1], ...pairs, [a.length, b.length]];
  for (let k = 0; k < bounds.length - 1; k++) {
    const [a0, b0] = bounds[k], [a1, b1] = bounds[k + 1];
    const ra = [], rb = [];
    for (let i = a0 + 1; i < a1; i++) ra.push(i);
    for (let j = b0 + 1; j < b1; j++) rb.push(j);
    for (let t = 0; t < Math.min(ra.length, rb.length); t++) {
      wordDiff(a[ra[t]], b[rb[t]], la[ra[t]], lb[rb[t]], delRanges, addRanges);
    }
  }
  if (window.CSS && CSS.highlights) {
    CSS.highlights.set("diff-del", new Highlight(...delRanges));
    CSS.highlights.set("diff-add", new Highlight(...addRanges));
  }
}

function wordDiff(x, y, lx, ly, delRanges, addRanges) {
  const tok = (s) => s.match(/\w+|\s+|[^\w\s]/g) || [];
  const ta = tok(x), tb = tok(y);
  const pairs = lcs(ta, tb, (p, q) => p === q);
  const shared = pairs.filter(([i]) => ta[i].trim()).length;
  const words = Math.max(ta.filter((t) => t.trim()).length, tb.filter((t) => t.trim()).length);
  if (!words || shared / words < 0.4) return;
  const keepA = new Set(pairs.map((p) => p[0])), keepB = new Set(pairs.map((p) => p[1]));
  const ranges = (tokens, keep, line, out) => {
    const tx = line?.querySelector(".tx");
    if (!tx) return;
    let pos = 0;
    tokens.forEach((t, i) => {
      if (!keep.has(i) && t.trim()) { const r = textRange(tx, pos, pos + t.length); if (r) out.push(r); }
      pos += t.length;
    });
  };
  ranges(ta, keepA, lx, delRanges);
  ranges(tb, keepB, ly, addRanges);
}

// ---------------------------------------------------------------- slide rendering

function renderSlide(slide) {
  const root = el("div", `slide k-${slide.kind}`);
  anchorEls = {};
  if (window.CSS && CSS.highlights) CSS.highlights.clear();
  if (slide.kind === "intro") {
    root.innerHTML = `<div class="rule"></div><h1>${inline(slide.title)}</h1>${slide.text ? `<p>${inline(slide.text)}</p>` : ""}`;
    anchorEls[slide.anchor] = root;
    return { root, ready: Promise.resolve() };
  }
  const head = el("div", "slide-head", `${slide.title ? `<h2>${inline(slide.title)}</h2>` : ""}${slide.text ? `<p>${inline(slide.text)}</p>` : ""}`);
  const body = el("div", "slide-body");
  root.append(head, body);
  anchorEls[slide.anchor] = body;
  let ready = Promise.resolve();

  switch (slide.kind) {
    case "cards": {
      const grid = el("div", "cards");
      const n = slide.entries.length;
      const cols = S.mode === "portrait" ? 1 : S.mode === "square" ? (n <= 2 ? n : 2) : (n <= 3 ? n : n === 4 ? 2 : 3);
      grid.style.gridTemplateColumns = `repeat(${Math.max(1, cols)}, 1fr)`;
      for (const e of slide.entries) {
        const card = el("div", "card entry", `<h3>${inline(e.title)}</h3>${e.text ? `<p>${inline(e.text)}</p>` : ""}`);
        if (e.anchor) anchorEls[e.anchor] = card;
        grid.append(card);
      }
      body.append(grid);
      break;
    }
    case "ul":
    case "ol": {
      const list = el("ol", `list ${slide.kind}`);
      for (const e of slide.entries) {
        const li = el("li", "entry", `<div>${e.title ? `<h3>${inline(e.title)}</h3>` : ""}${e.text ? `<p>${inline(e.text)}</p>` : ""}</div>`);
        if (e.anchor) anchorEls[e.anchor] = li;
        list.append(li);
      }
      body.append(list);
      break;
    }
    case "table": {
      const t = el("table", "data");
      t.innerHTML = `<thead><tr>${slide.table.header.map((h) => `<th>${inline(h)}</th>`).join("")}</tr></thead>` +
        `<tbody>${slide.table.rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      body.append(t);
      break;
    }
    case "code": {
      const box = codeBox(slide.code, slide.language, slide.filename, null);
      box.dataset.code = "1";
      anchorEls[slide.anchor] = box;
      body.append(box);
      break;
    }
    case "diff": {
      const wrap = el("div", "diff");
      const [b, a] = slide.sides;
      const boxes = [b, a].map((side, i) => {
        const fallbackTag = i === 0 ? "Before" : "After";
        const box = codeBox(side?.code || "", slide.language, side?.filename || "", side?.title || fallbackTag);
        box.dataset.code = "1";
        if (side?.anchor) anchorEls[side.anchor] = box;
        wrap.append(box);
        return box;
      });
      body.append(wrap);
      ready = new Promise((res) => requestAnimationFrame(() => { markDiff(boxes[0], boxes[1], b?.code || "", a?.code || ""); res(); }));
      break;
    }
    case "diagram": {
      const d = el("div", "diagram");
      body.append(d);
      ready = renderDiagram(d, slide);
      break;
    }
    case "math": {
      const m = el("div", "math");
      try {
        katex.render(slide.latex, m, { displayMode: true, throwOnError: true });
      } catch (e) {
        report(`Math #${slide.anchor} failed to render: ${e.message}`);
        katex.render(slide.latex, m, { displayMode: true, throwOnError: false });
      }
      body.append(m);
      break;
    }
    case "image": {
      const wrap = el("div", "image");
      const img = el("img");
      img.alt = slide.alt || slide.title || "";
      img.src = slide.src;
      wrap.append(img);
      body.append(wrap);
      ready = img.decode().catch(() => report(`Image #${slide.anchor} failed to load.`));
      break;
    }
    case "text":
      body.append(el("div", "textbody", inline(slide.body || "")));
      break;
    default:
      body.append(el("div", "unsupported", `This slide type (${esc(slide.requested || slide.kind)}) is not supported by this player.`));
  }
  return { root, ready };
}

let diagramSeq = 0;
async function renderDiagram(container, slide) {
  try {
    const { svg } = await mermaid.render(`mmd-${++diagramSeq}`, slide.source);
    container.innerHTML = svg;
    const s = container.querySelector("svg");
    const vb = s.viewBox.baseVal;
    const box = container.getBoundingClientRect();
    const scale = parseFloat(stage.dataset.scale) || 1;
    const bw = box.width / scale, bh = box.height / scale;
    const k = Math.min(bw / vb.width, bh / vb.height, 1.7);
    s.style.maxWidth = "none";
    s.setAttribute("width", vb.width * k);
    s.setAttribute("height", vb.height * k);
  } catch (e) {
    container.innerHTML = `<div class="err">Diagram failed to render.</div>`;
    report(`Diagram #${slide.anchor} failed to render: ${String(e.message || e).split("\n")[0]}`);
    document.querySelectorAll('[id^="dmmd-"]').forEach((n) => n.remove());
  }
}

function showSlide(i) {
  if (i === shownSlide) return;
  shownSlide = i;
  const slide = S.slides[i];
  host.innerHTML = "";
  hidePointer();
  const { root, ready } = renderSlide(slide);
  host.append(root);
  const body = root.querySelector(".slide-body");
  slideReady = ready.then(() => { if (body) fit(body); });
  renderProgress();
}

// ---------------------------------------------------------------- pointer

let lastTarget = null, lastWide = false;

function hidePointer() {
  pointer.hidden = true;
  lastTarget = null;
  host.querySelectorAll(".entry.active").forEach((e) => e.classList.remove("active"));
  host.querySelectorAll(".has-active").forEach((e) => e.classList.remove("has-active"));
  host.querySelectorAll(".ln.hl").forEach((e) => e.classList.remove("hl"));
  if (window.CSS && CSS.highlights) CSS.highlights.delete("ptr");
}

function unionRect(rects) {
  rects = [...rects].filter((r) => r.width || r.height);
  if (!rects.length) return null;
  const l = Math.min(...rects.map((r) => r.left)), t = Math.min(...rects.map((r) => r.top));
  const r = Math.max(...rects.map((r) => r.right)), b = Math.max(...rects.map((r) => r.bottom));
  return { left: l, top: t, width: r - l, height: b - t };
}

function placePointer(target, wide) {
  const rect = target.getBoundingClientRect ? unionRect([target.getBoundingClientRect()]) : unionRect(target.getClientRects());
  if (!rect) return;
  const sr = stage.getBoundingClientRect();
  const scale = parseFloat(stage.dataset.scale) || 1;
  const pad = wide ? 10 : 6;
  pointer.style.left = `${(rect.left - sr.left) / scale - pad}px`;
  pointer.style.top = `${(rect.top - sr.top) / scale - pad}px`;
  pointer.style.width = `${rect.width / scale + pad * 2}px`;
  pointer.style.height = `${rect.height / scale + pad * 2}px`;
  pointer.classList.toggle("wide", !!wide);
  pointer.hidden = false;
  lastTarget = target;
  lastWide = wide;
}

async function applyCue(cue, sayIndex) {
  await slideReady;
  if (sayIndex !== cur) return;
  const target = anchorEls[cue.anchor];
  if (!target) return;
  hidePointer();
  if (target.classList.contains("entry")) {
    target.classList.add("active");
    target.parentElement.classList.add("has-active");
    placePointer(target, false);
    return;
  }
  if (target.dataset.code) {
    const lines = lineEls(target);
    const from = cue.line ? cue.line - 1 : 0;
    const to = cue.line ? (cue.line_end || cue.line) - 1 : lines.length - 1;
    if (cue.sel) {
      for (let i = from; i <= to && i < lines.length; i++) {
        const tx = lines[i].querySelector(".tx");
        const idx = tx.textContent.indexOf(cue.sel);
        if (idx >= 0) {
          const r = textRange(tx, idx, idx + cue.sel.length);
          if (r) {
            if (window.CSS && CSS.highlights) CSS.highlights.set("ptr", new Highlight(r));
            lines[i].classList.add("hl");
            placePointer(r, false);
            return;
          }
        }
      }
    }
    if (cue.line) {
      const sel = lines.slice(from, to + 1);
      sel.forEach((l) => l.classList.add("hl"));
      placePointer({ getClientRects: () => sel.map((l) => l.getBoundingClientRect()) }, false);
      return;
    }
    placePointer(target, true);
    return;
  }
  const svg = target.querySelector?.(".diagram svg");
  if (svg && cue.sel) {
    const found = findInDiagram(svg, cue.sel);
    if (found) { placePointer(found, false); return; }
  }
  if (S.slides[shownSlide]?.kind !== "intro") {
    const visual = target.classList.contains("slide-body") ? target.firstElementChild || target : target;
    placePointer(visual.querySelector?.(".diagram svg, .math .katex-display, .image img") || visual, true);
  }
}

function findInDiagram(svg, label) {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim().toLowerCase();
  const want = norm(label);
  const groups = [...svg.querySelectorAll("g.node, g.edgeLabel, g.cluster-label, g.cluster, text, foreignObject")];
  const pick = (n) => n.closest("g.node, g.edgeLabel") || (n.tagName === "foreignObject" ? n : n.closest("g.cluster")) || n;
  const exact = groups.find((g) => norm(g.textContent) === want);
  if (exact) return pick(exact);
  const leaves = groups.filter((g) => g.tagName === "text" || g.tagName === "foreignObject");
  const partial = leaves.find((g) => norm(g.textContent).includes(want));
  return partial ? pick(partial) : null;
}

// ---------------------------------------------------------------- captions

function renderCaption(say) {
  const cap = $("#caption");
  if (!say) { cap.innerHTML = ""; return; }
  const text = say.plain;
  let html = "", pos = 0;
  (say.words || []).forEach((w, i) => {
    if (w.c0 < pos || w.c0 < 0) return;
    html += esc(text.slice(pos, w.c0)) + `<span class="w" data-i="${i}">${esc(text.slice(w.c0, w.c1))}</span>`;
    pos = w.c1;
  });
  html += esc(text.slice(pos));
  cap.innerHTML = `<div class="inner">${html}</div>`;
}

let lastWord = -1;
function updateCaption(say, t) {
  let idx = -1;
  const words = say.words || [];
  for (let i = 0; i < words.length; i++) { if (words[i].s <= t) idx = i; else break; }
  if (idx === lastWord) return;
  $("#caption .w.now")?.classList.remove("now");
  const w = $(`#caption .w[data-i="${idx}"]`);
  if (w) w.classList.add("now");
  lastWord = idx;
}

// ---------------------------------------------------------------- playback

function setWaiting(on, text) {
  waiting = on;
  $("#waiting").hidden = !on;
  if (text) $("#waiting span").textContent = text;
}

function setPlayIcon() {
  $("#play-icon").setAttribute("d", playing ? "M6 5h4v14H6zM14 5h4v14h-4z" : "M8 5v14l11-7z");
}

function playFrom(i) {
  clearTimeout(advanceTimer);
  cancelAnimationFrame(raf);
  cur = i;
  $("#end").hidden = true;
  const say = S.says[i];
  if (!say) {
    if (S.status === "finished" && S.says.length) {
      setWaiting(false);
      showEnd();
    } else {
      setWaiting(true, "Writing the next slide…");
    }
    return;
  }
  showSlide(say.slide);
  if (say.status === "pending") { setWaiting(true, "Voicing the narration…"); renderCaption(null); return; }
  setWaiting(false);
  fired = new Set();
  lastWord = -1;
  renderCaption(say);
  if (say.status === "error") {
    report(`Narration failed for say ${say.id}: ${say.error || "unknown error"}`);
    advanceTimer = setTimeout(() => playing && playFrom(i + 1), 1500);
    return;
  }
  // Cues at time 0 fire immediately so the slide's opening link lands with the first word.
  audio.src = say.audio;
  audio.playbackRate = speed;
  audio.play().catch(() => { playing = false; setPlayIcon(); showOverlay(); });
  const next = S.says[i + 1];
  if (next?.audio) prefetch.src = next.audio;
  tick();
}

function tick() {
  const say = S.says[cur];
  if (!say || !playing) return;
  const t = audio.currentTime;
  say.cues.forEach((c, k) => {
    if (!fired.has(k) && (c.t ?? 0) <= t + 0.08) {
      fired.add(k);
      applyCue(c, cur);
    }
  });
  updateCaption(say, t);
  updateTime();
  raf = requestAnimationFrame(tick);
}

audio.addEventListener("ended", () => {
  cancelAnimationFrame(raf);
  if (!playing) return;
  advanceTimer = setTimeout(() => playing && playFrom(cur + 1), GAP_MS / speed);
});

function play() {
  if (!S || !S.says.length) return;
  playing = true;
  started = true;
  $("#overlay").hidden = true;
  setPlayIcon();
  const say = S.says[cur];
  if (say && audio.src && audio.src.endsWith(say.audio) && !audio.ended && audio.currentTime > 0) {
    audio.play();
    tick();
  } else {
    playFrom(cur);
  }
}

function pause() {
  playing = false;
  audio.pause();
  clearTimeout(advanceTimer);
  cancelAnimationFrame(raf);
  setPlayIcon();
}

function jumpToSlide(slideIdx) {
  const i = S.says.findIndex((s) => s.slide === slideIdx);
  if (i < 0) return;
  audio.pause();
  if (!playing) { playing = true; started = true; $("#overlay").hidden = true; setPlayIcon(); }
  playFrom(i);
}

function slideOf(i) { return S.says[i]?.slide ?? shownSlide; }

function showEnd() {
  pause();
  const box = $("#followups");
  box.innerHTML = "";
  for (const f of S.followups) {
    const b = el("button", null, esc(f));
    b.onclick = () => {
      navigator.clipboard?.writeText(f).then(() => { b.textContent = "Copied: " + f; }).catch(() => {});
    };
    box.append(b);
  }
  if (!S.followups.length) box.innerHTML = "<p>That's the end.</p>";
  $("#end").hidden = false;
}

function showOverlay() {
  $("#overlay").hidden = false;
  $("#overlay-title").textContent = S?.title || "";
  const ready = S?.says?.[0]?.status === "ready";
  $("#big-play").disabled = !ready;
  $("#overlay-sub").textContent = ready
    ? (S.status === "finished" ? `${S.slides.length} slides · ${mmss(totalDuration())}` : "Still being written. Press play and it will keep up.")
    : "Preparing the first slide…";
}

// ---------------------------------------------------------------- progress / status

function totalDuration() {
  return S.says.reduce((a, s) => a + sayDuration(s), 0);
}

function elapsed() {
  let t = 0;
  for (let i = 0; i < cur && i < S.says.length; i++) t += sayDuration(S.says[i]);
  return t + (S.says[cur]?.status === "ready" ? audio.currentTime : 0);
}

function updateTime() {
  if (!S) return;
  const more = S.status !== "finished" ? "+" : "";
  $("#time").textContent = `${mmss(elapsed())} / ${mmss(totalDuration())}${more}`;
  const segs = $("#progress").children;
  const per = slideDurations();
  let acc = 0;
  const e = elapsed();
  for (let k = 0; k < segs.length; k++) {
    const d = per[k] || 0;
    const fill = d ? Math.max(0, Math.min(1, (e - acc) / d)) : (k < slideOf(cur) ? 1 : 0);
    segs[k].firstChild.style.width = `${fill * 100}%`;
    acc += d;
  }
}

function slideDurations() {
  const per = new Array(S.slides.length).fill(0);
  for (const s of S.says) per[s.slide] += sayDuration(s);
  return per;
}

function renderProgress() {
  if (!S) return;
  const bar = $("#progress");
  const per = slideDurations();
  while (bar.children.length < S.slides.length) {
    const k = bar.children.length;
    const seg = el("div", "seg", '<div class="fill"></div>');
    seg.onclick = () => jumpToSlide(k);
    bar.append(seg);
  }
  [...bar.children].forEach((seg, k) => {
    seg.style.flex = `${Math.max(per[k], 1)} 1 0`;
    seg.title = S.slides[k]?.title || "";
    seg.classList.toggle("pending", S.says.some((s) => s.slide === k && s.status === "pending"));
  });
  const pending = S.says.filter((s) => s.status === "pending").length;
  $("#status").textContent = S.status !== "finished" ? `Writing · ${S.slides.length} slides` : pending ? `Voicing ${pending}…` : "";
  updateTime();
}

// ---------------------------------------------------------------- live updates

function onSnapshot(snap) {
  const first = !S;
  S = snap;
  if (first) {
    document.body.classList.add(S.mode);
    layoutStage();
    $("#overlay-title").textContent = S.title;
  }
  if (!started) {
    if (S.slides.length && shownSlide < 0) showSlide(0);
    showOverlay();
  }
  renderProgress();
  if (playing && waiting) playFrom(cur);
  if (playing && !waiting && S.says[cur + 1]?.audio && !prefetch.src.endsWith(S.says[cur + 1].audio)) {
    prefetch.src = S.says[cur + 1].audio;
  }
}

function connect() {
  const es = new EventSource(`/api/e/${ID}/events`);
  es.onmessage = (ev) => onSnapshot(JSON.parse(ev.data));
  es.onerror = () => { $("#status").textContent = "Reconnecting…"; };
}

// ---------------------------------------------------------------- controls

$("#big-play").onclick = play;
$("#play").onclick = () => (playing ? pause() : play());
$("#prev").onclick = () => S && jumpToSlide(Math.max(0, slideOf(cur) - (audio.currentTime > 2 ? 0 : 1)));
$("#next").onclick = () => S && jumpToSlide(slideOf(cur) + 1);
$("#replay").onclick = () => { $("#end").hidden = true; jumpToSlide(0); };
$("#speed").value = String(speed);
$("#speed").onchange = (e) => {
  speed = parseFloat(e.target.value) || 1;
  audio.playbackRate = speed;
  try { localStorage.setItem("explain.speed", speed); } catch (err) { /* ignore */ }
};
function setCaptions(on) {
  captionsOn = on;
  $("#caption").classList.toggle("off", !on);
  $("#cc").classList.toggle("on", on);
  try { localStorage.setItem("explain.cc", on ? "1" : "0"); } catch (err) { /* ignore */ }
  layoutStage();
}
$("#cc").onclick = () => setCaptions(!captionsOn);
setCaptions(captionsOn);
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT") return;
  if (e.key === " ") { e.preventDefault(); playing ? pause() : play(); }
  else if (e.key === "ArrowRight") $("#next").click();
  else if (e.key === "ArrowLeft") $("#prev").click();
  else if (e.key === "c") setCaptions(!captionsOn);
});

fetch(`/api/e/${ID}`).then((r) => r.json()).then((snap) => { onSnapshot(snap); connect(); });
