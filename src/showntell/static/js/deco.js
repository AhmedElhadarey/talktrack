// Decoration: icons the builder copied into the slide (Lucide, drawn with the text colour), and
// the motif behind title and chapter slides, placed by a hash of the title so every walkthrough
// gets its own arrangement in the theme's palette.

export function icon(slide, name, cls = "") {
  const inner = name && slide.icons?.[name];
  if (!inner) return "";
  return `<svg class="ic${cls ? ` ${cls}` : ""}" viewBox="0 0 24 24" aria-hidden="true">${inner}</svg>`;
}

// `:name: text` at the start of shown text: the icon and the rest.
export function leadingIcon(text) {
  const m = /^:([a-z0-9-]+):\s*/.exec(text || "");
  return m ? [m[1], text.slice(m[0].length)] : ["", text || ""];
}

function rng(seed) {
  let h = 2166136261;
  for (const c of seed) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}

// One of three arrangements, kept to the side away from the text: rings around a disc (or the
// title's icon), a stack of rounded bars, or arcs from a corner; each with a small grid of dots.
export function motif(seed, [w, h], iconSvg = "") {
  const r = rng(seed || "showntell");
  const tall = h > w;
  const style = iconSvg ? 0 : Math.floor(r() * 3);
  const c = (k) => `var(--c${1 + ((k + Math.floor(r() * 6)) % 6)})`;
  const ca = c(0), cb = c(2), cc = c(4);
  const gx = tall ? w * 0.1 : w * (0.64 + r() * 0.04), gy = tall ? h * 0.44 : h * (0.76 + r() * 0.04);
  let dots = "";
  for (let i = 0; i < 6; i++) for (let j = 0; j < 4; j++) dots += `<circle cx="${gx + i * 26}" cy="${gy + j * 26}" r="3.2"/>`;
  let art;
  if (style === 1) {
    // Rounded bars of different lengths, leaning out from the right edge.
    const x0 = tall ? w * 0.18 : w * 0.66, y0 = tall ? h * 0.06 : h * 0.14, bh = Math.min(w, h) * 0.075;
    art = [0, 1, 2, 3, 4].map((i) => {
      const len = (tall ? w * 0.7 : w * 0.3) * (0.45 + r() * 0.55);
      return `<rect x="${(tall ? x0 : w - len - w * 0.04).toFixed(1)}" y="${(y0 + i * bh * 1.45).toFixed(1)}" width="${len.toFixed(1)}" height="${bh.toFixed(1)}" rx="${(bh / 2).toFixed(1)}" fill="${[ca, cb, cc][i % 3]}" opacity="${(0.85 - i * 0.13).toFixed(2)}"/>`;
    }).join("");
  } else if (style === 2) {
    // Quarter arcs from the top right corner, thick then thin.
    const R = Math.min(w, h) * 0.95;
    art = [1, 0.8, 0.62, 0.46].map((k, i) => `<circle class="ring" cx="${w}" cy="0" r="${R * k}" stroke="${[ca, cb, cc, ca][i]}" stroke-width="${[26, 3, 14, 2][i]}" opacity="${[0.9, 0.5, 0.7, 0.4][i]}"/>`).join("");
  } else {
    const cx = tall ? w * (0.5 + r() * 0.2) : w * (0.8 + r() * 0.08), cy = tall ? h * (0.22 + r() * 0.08) : h * (0.34 + r() * 0.3);
    const base = Math.min(w, h) * (0.18 + r() * 0.06);
    const rings = [1, 1.45, 1.9, 2.4].map((k, i) => `<circle class="ring" cx="${cx}" cy="${cy}" r="${base * k}" stroke="${i % 2 ? cb : ca}" opacity="${0.5 - i * 0.1}"/>`).join("");
    const start = r() * Math.PI * 2, sweep = Math.PI * (0.5 + r() * 0.5), R = base * 1.45;
    const p = (a) => `${cx + R * Math.cos(a)} ${cy + R * Math.sin(a)}`;
    const arc = `<path class="arc" d="M${p(start)} A${R} ${R} 0 ${sweep > Math.PI ? 1 : 0} 1 ${p(start + sweep)}" stroke="${ca}"/>`;
    const big = iconSvg
      ? `<g class="bigicon" transform="translate(${cx - base * 0.62} ${cy - base * 0.62}) scale(${(base * 1.24) / 24})" stroke="${ca}" stroke-width="1.4">${iconSvg}</g>`
      : `<circle class="disc" cx="${cx}" cy="${cy}" r="${base * 0.78}" fill="${cc}"/>`;
    art = rings + arc + big;
  }
  return `<div class="motif" aria-hidden="true"><svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid slice">${art}<g class="dots" fill="${cb}">${dots}</g></svg></div>`;
}
