// Other windows: presenting without the voice (each press moves to the next step, the narration
// becomes speaker notes), an audience window that follows the presenter, and commands from a page
// that embeds the player (see embed.js; events go out through `emit` in state.js).

import { mmss, stateAt, steps } from "./timeline.js";
import { $, body, now, PRESENT, S } from "./state.js";
import { pause, play, seek } from "./playback.js";
import { setTranscript, stripMd } from "./chrome.js";
import { rtl } from "./draw.js";
import { captionHtml } from "./util.js";

let channel = null, presentStarted = 0;

export function startPresenting() {
  const { W } = S;
  body.classList.add("presenter");
  $("#notes").hidden = false;
  setTranscript(false);
  $("#overlay").hidden = true;
  S.started = true;
  channel = "BroadcastChannel" in window ? new BroadcastChannel(`talktrack:${W.id}`) : null;
  $("#notes-audience").onclick = () => {
    const u = new URL(location.href);
    u.searchParams.delete("present");
    u.searchParams.set("audience", "1");
    window.open(u.toString(), `talktrack-audience-${W.id}`, "popup");
  };
  channel?.addEventListener("message", (e) => { if (e.data?.hello) channel.postMessage({ t: now() }); });
  setInterval(() => { if (presentStarted) $("#notes-clock").textContent = mmss((performance.now() - presentStarted) / 1000); }, 500);
  presenterSync();
}

// The step we are on is the last one at or before the current time.
function stepIndex(all, t) {
  let i = 0;
  for (let k = 0; k < all.length; k++) if (all[k].t <= t + 0.01) i = k;
  return i;
}

export function stepBy(d) {
  const all = steps(S.W, S.TL);
  if (!all.length) return;
  const i = Math.max(0, Math.min(all.length - 1, stepIndex(all, now()) + d));
  if (!presentStarted) presentStarted = performance.now();
  seek(all[i].t);
}

export function presenterSync() {
  const { W, TL } = S;
  if (!PRESENT || !W) return;
  const all = steps(W, TL);
  const t = now();
  const i = stepIndex(all, t);
  const st = stateAt(W, TL, t);
  const slide = W.slides[st.slide];
  if (!slide) return;
  const paras = slide.says.filter((x) => x.text);
  const current = all[i]?.say ?? -1;
  $("#notes-step").textContent = `Slide ${st.slide + 1} of ${W.slides.length} · step ${i + 1} of ${all.length}`;
  $("#notes-body").innerHTML = paras.map((x) => `<p class="${slide.says.indexOf(x) === current ? "now" : ""}">${captionHtml({ text: x.text, code: x.code }, rtl())}</p>`).join("");
  const next = W.slides[st.slide + 1];
  $("#notes-next").textContent = next ? `Next: ${stripMd(next.title)}` : "Last slide";
  channel?.postMessage({ t });
}

export function followPresenter() {
  body.classList.add("audience");
  $("#overlay").hidden = true;
  S.started = true;
  const ch = "BroadcastChannel" in window ? new BroadcastChannel(`talktrack:${S.W.id}`) : null;
  ch?.addEventListener("message", (e) => { if (Number.isFinite(e.data?.t)) seek(e.data.t); });
  ch?.postMessage({ hello: true });
}

// Commands from the page that embeds the player in an iframe.
window.addEventListener("message", (e) => {
  const m = e.data;
  if (!m || m.target !== "talktrack" || e.source !== window.parent) return;
  if (m.type === "play") play();
  else if (m.type === "pause") pause();
  else if (m.type === "seek" && Number.isFinite(m.t)) seek(m.t);
});
