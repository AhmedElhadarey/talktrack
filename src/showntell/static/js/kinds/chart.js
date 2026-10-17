// Bar, horizontal bar and line charts, drawn as SVG with notes as callouts. Bars and series are
// the parts cues point at.

import { el } from "../draw.js";
import { esc } from "../util.js";

// Drawing size per frame, in SVG units: close to the slide body's shape, so text keeps its size.
const SIZES = { landscape: [1100, 600], square: [900, 820], portrait: [660, 940] };
const PALETTE = ["var(--accent)", "var(--node-line)", "var(--add-fg)", "var(--hl-title)", "var(--hl-built-in)", "var(--del-fg)"];

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const c = el("div", "chart");
    c.setAttribute("role", "img");
    c.setAttribute("aria-label", chartLabel(v));
    c.innerHTML = drawChart(v, SIZES[ctx?.frame] || SIZES.landscape);
    c.querySelectorAll("g.entry").forEach((g) => parts.items.set(g.dataset.id, g));
    parts.visual = c;
    parts.svg = null;
    body.append(c);
  },
};

function chartLabel(v) {
  const head = v.title ? `${v.title}. ` : "";
  if (v.type === "line") return `${head}Line chart of ${v.series.map((s) => s.name).join(", ")} over ${v.x.join(", ")}.`;
  return `${head}Bar chart: ${v.categories.map((c) => `${c.label} ${c.values.join(" and ")}${v.unit ? ` ${v.unit}` : ""}`).join("; ")}.`;
}

// A round axis maximum and step: 1, 2 or 5 times a power of ten.
function niceScale(max, ticks = 5) {
  if (!(max > 0)) return { top: 1, step: 0.2 };
  const raw = max / ticks;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
  return { top: Math.ceil(max / step - 1e-9) * step, step };
}

function fmt(n, unit) {
  const abs = Math.abs(n);
  const s = abs >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : abs >= 1e4 ? `${+(n / 1e3).toFixed(1)}k` : `${+n.toFixed(abs < 10 ? 2 : abs < 100 ? 1 : 0)}`;
  return unit ? `${s} ${unit}` : s;
}

function drawChart(v, [W, H]) {
  const t = (x, y, text, cls, anchor = "middle") => `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${esc(text)}</text>`;
  const all = v.type === "line" ? v.series.flatMap((s) => s.values) : v.categories.flatMap((c) => c.values);
  const lo = Math.min(0, ...all);
  const { top, step } = niceScale(Math.max(...all, 0) - lo);
  const head = v.title ? t(W / 2, 34, v.title, "ctitle") : "";
  const m = { l: 96, r: 40, t: v.title ? 70 : 30, b: 86 };
  if (v.type === "hbar") return drawHbar(v, W, H, m, top, step, head, t);
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const y = (val) => m.t + ih - ((val - lo) / top) * ih;
  let grid = "";
  for (let k = 0; k <= top / step + 1e-9; k++) {
    const val = lo + k * step;
    grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(val)}" y2="${y(val)}" class="grid"/>` + t(m.l - 12, y(val) + 7, fmt(val, ""), "tick", "end");
  }
  const axes = (v.ylabel ? `<text class="alabel" text-anchor="middle" transform="translate(24 ${m.t + ih / 2}) rotate(-90)">${esc(v.ylabel + (v.unit ? ` (${v.unit})` : ""))}</text>` : "")
    + (v.xlabel ? t(m.l + iw / 2, H - 14, v.xlabel, "alabel") : "");
  let marks = "";
  if (v.type === "line") {
    const n = v.x.length;
    const x = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    v.x.forEach((xv, i) => { marks += t(x(i), m.t + ih + 34, xv, "tick"); });
    v.series.forEach((s, si) => {
      const col = PALETTE[si % PALETTE.length];
      const pts = s.values.map((val, i) => `${x(i)},${y(val)}`).join(" ");
      const last = s.values.length - 1;
      marks += `<g class="entry series" data-id="${esc(s.id)}" data-idx="${si}" style="--c:${col}">`
        + `<polyline points="${pts}" class="line"/>`
        + s.values.map((val, i) => `<circle cx="${x(i)}" cy="${y(val)}" r="7" class="dot"><title>${esc(`${s.name}: ${fmt(val, v.unit)}`)}</title></circle>`).join("")
        + (last >= 0 ? t(Math.min(W - m.r, x(last)) - 8, y(s.values[last]) - 16, s.name, "slabel", "end") : "")
        + "</g>";
    });
    (v.notes || []).forEach((note) => {
      const vals = v.series.filter((s) => !note.series || s.id === note.series).map((s) => s.values[note.x]).filter((x) => x !== undefined);
      if (!vals.length) return;
      marks += noteMark(note, x(note.x), y(Math.max(...vals)), m, W, t);
    });
  } else {
    const n = v.categories.length, k = Math.max(1, ...v.categories.map((c) => c.values.length));
    const band = iw / Math.max(1, n), gap = band * 0.24, bw = (band - gap) / k;
    v.categories.forEach((c, ci) => {
      let g = `<g class="entry bar" data-id="${esc(c.id)}" data-idx="${ci}">`;
      c.values.forEach((val, si) => {
        const x0 = m.l + ci * band + gap / 2 + si * bw;
        const y0 = y(Math.max(0, val)), h = Math.abs(y(val) - y(0));
        g += `<rect x="${x0}" y="${y0}" width="${Math.max(2, bw - 6)}" height="${Math.max(1, h)}" rx="6" style="fill:${PALETTE[(k > 1 ? si : 0) % PALETTE.length]}"/>`
          + t(x0 + (bw - 6) / 2, y0 - 12, fmt(val, v.unit), "vlabel");
      });
      g += t(m.l + ci * band + band / 2, m.t + ih + 36, c.label, "tick") + "</g>";
      marks += g;
    });
    if (k > 1) marks += legend(v.series, m.l, H - 22);
    (v.notes || []).forEach((note) => {
      const ci = v.categories.findIndex((c) => c.id === note.bar);
      if (ci < 0) return;
      const c = v.categories[ci];
      marks += noteMark(note, m.l + ci * band + band / 2, y(Math.max(...c.values, 0)) - 50, m, W, t);
    });
  }
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${head}${grid}${axes}<line x1="${m.l}" x2="${W - m.r}" y1="${y(Math.max(lo, 0))}" y2="${y(Math.max(lo, 0))}" class="axis"/>${marks}</svg>`;
}

// A callout: a short leader up from the point and the note's text.
function noteMark(note, px, py, m, W, t) {
  const ty = Math.max(m.t + 24, py - 64);
  const anchor = px > W * 0.7 ? "end" : px < W * 0.3 ? "start" : "middle";
  return `<g class="entry note" data-id="${esc(note.id)}"><line x1="${px}" y1="${py - 10}" x2="${px}" y2="${ty + 10}" class="leader"/>`
    + `<circle cx="${px}" cy="${py}" r="11" class="ring"/>${t(px, ty, note.text, "ntext", anchor)}</g>`;
}

function drawHbar(v, W, H, m, top, step, head, t) {
  const labelW = Math.min(360, 24 + 13 * Math.max(...v.categories.map((c) => c.label.length)));
  m.l = labelW;
  const iw = W - m.l - m.r - 90, ih = H - m.t - 40;
  const n = v.categories.length, band = ih / Math.max(1, n), bh = band * 0.66;
  const x = (val) => m.l + (val / top) * iw;
  let marks = "";
  v.categories.forEach((c, ci) => {
    const val = c.values[0] ?? 0, y0 = m.t + ci * band + (band - bh) / 2;
    marks += `<g class="entry bar" data-id="${esc(c.id)}" data-idx="${ci}">`
      + t(m.l - 14, y0 + bh / 2 + 8, c.label, "tick", "end")
      + `<rect x="${m.l}" y="${y0}" width="${Math.max(2, x(val) - m.l)}" height="${bh}" rx="6" style="fill:${PALETTE[0]}"/>`
      + t(x(val) + 12, y0 + bh / 2 + 8, fmt(val, v.unit), "vlabel", "start") + "</g>";
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${head}<line x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${m.t + ih}" class="axis"/>${marks}</svg>`;
}

function legend(series, x, y) {
  let out = "", cx = x;
  series.forEach((s, i) => {
    out += `<rect x="${cx}" y="${y - 16}" width="18" height="18" rx="4" style="fill:${PALETTE[i % PALETTE.length]}"/><text x="${cx + 26}" y="${y}" class="legend">${esc(s.name)}</text>`;
    cx += 60 + s.name.length * 12;
  });
  return out;
}
