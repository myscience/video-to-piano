// pianoscribe practice viewer: the score, with the notes lighting up as the original recording plays.
//
// Positions: the recording's time <-> beats (the tracked beat grid, so rubato is followed) <->
// score position q, in quarter notes from the start of bar 1 (Verovio's timemap).

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const LEAD_IN = 1.0; // seconds of recording before bar 1 when starting from the top

let S = null; // the loaded song

// ---- time mapping --------------------------------------------------------------------------

function beatAt(t) {
  // Recording time (s) -> beats from the first downbeat: linear between tracked beats,
  // extended past both ends with the edge beat's length.
  const B = S.sync.beats;
  let lo = 0, hi = B.length - 2;
  if (t >= B[B.length - 1]) lo = B.length - 2;
  else if (t > B[0]) {
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (B[mid] <= t) lo = mid; else hi = mid - 1;
    }
  }
  return lo + (t - B[lo]) / (B[lo + 1] - B[lo]) - S.sync.first_downbeat;
}

function timeAt(beat) {
  const B = S.sync.beats;
  const x = beat + S.sync.first_downbeat;
  const i = Math.min(Math.max(Math.floor(x), 0), B.length - 2);
  return B[i] + (x - i) * (B[i + 1] - B[i]);
}

const qAt = (t) => beatAt(t) + S.sync.shift_beats; // recording time -> score position
const tAt = (q) => timeAt(q - S.sync.shift_beats); // score position -> recording time
const barOf = (q) => Math.floor(q / S.sync.beats_per_bar);

// ---- loading -------------------------------------------------------------------------------

async function loadSongs() {
  const songs = await (await fetch("/api/songs")).json();
  const select = $("song");
  select.innerHTML = songs.map((s) => `<option value="${s.slug}">${s.title}</option>`).join("");
  if (!songs.length) {
    $("score").innerHTML = '<p class="muted empty">No songs yet: run <code>pianoscribe score &lt;song&gt;</code>.</p>';
    return;
  }
  const wanted = decodeURIComponent(location.hash.slice(1));
  select.value = songs.some((s) => s.slug === wanted) ? wanted : songs[0].slug;
  await loadSong(select.value);
}

async function loadSong(slug) {
  audio.pause();
  const base = `/api/songs/${encodeURIComponent(slug)}/view/`;
  const [sync, notes] = await Promise.all([
    fetch(base + "sync.json").then((r) => r.json()),
    fetch(base + "notes.json").then((r) => r.json()),
  ]);
  const pages = await Promise.all(
    Array.from({ length: sync.pages }, (_, i) => fetch(`${base}page-${i + 1}.svg`).then((r) => r.text())),
  );
  S = { slug, base, sync, notes, svg: { pages, line: null }, spans: [], byId: new Map(), active: new Set(),
        loop: { a: null, b: null }, system: null, anchors: null, lastQ: null };

  $("credits").textContent = sync.composer || "";
  $("pdf").href = `/api/songs/${encodeURIComponent(slug)}/pdf`;
  document.title = `${sync.title || slug} · pianoscribe`;
  history.replaceState(null, "", `#${encodeURIComponent(slug)}`);
  showLoop();
  await render();
  audio.src = `/api/songs/${encodeURIComponent(slug)}/audio`;
  audio.addEventListener("loadedmetadata", () => { audio.currentTime = Math.max(0, tAt(0) - LEAD_IN); },
                         { once: true });
}

// ---- views ---------------------------------------------------------------------------------

let mode = "pages";
try { mode = localStorage.getItem("pianoscribe.mode") === "line" ? "line" : "pages"; } catch {}

async function render() {
  // Draw the score in the current view mode and (re)bind the notes to their SVG elements.
  const main = $("score");
  if (mode === "line") {
    S.svg.line ??= await fetch(S.base + "line.svg").then((r) => r.text());
    main.className = "line";
    main.innerHTML = `<div class="stripwrap"><div class="strip" id="strip"><div class="strip-inner">` +
                     `<div class="pad"></div>${S.svg.line}<div class="pad"></div></div></div>` +
                     `<div class="playhead"></div></div>`;
  } else {
    main.className = "";
    main.innerHTML = S.svg.pages.map((svg) => `<div class="page">${svg}</div>`).join("");
  }
  S.spans = Object.entries(S.notes)
    .map(([id, [on, off]]) => ({ id, on, off: Math.max(off, on + 0.05), el: document.getElementById(id) }))
    .filter((n) => n.el)
    .sort((a, b) => a.on - b.on);
  S.byId = new Map(S.spans.map((n) => [n.id, n]));
  S.active = new Set();
  S.system = null;
  S.lastQ = null;
  S.anchors = mode === "line" ? lineAnchors() : null;
  $("modePages").classList.toggle("on", mode === "pages");
  $("modeLine").classList.toggle("on", mode === "line");
}

function setMode(m) {
  if (m === mode || !S) return;
  mode = m;
  try { localStorage.setItem("pianoscribe.mode", m); } catch {}
  render();
}

function lineAnchors() {
  // (q, x) pairs along the endless system: the leftmost notehead at each score position, in px
  // from the start of the strip. Kept increasing so interpolation never runs backwards.
  const inner = document.querySelector(".strip-inner");
  const left = inner.getBoundingClientRect().left;
  const byQ = new Map();
  for (const n of S.spans) {
    const r = (n.el.querySelector(".notehead") ?? n.el).getBoundingClientRect();
    const x = r.left + r.width / 2 - left;
    if (!byQ.has(n.on) || x < byQ.get(n.on)) byQ.set(n.on, x);
  }
  const anchors = [...byQ.entries()].sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < anchors.length; i++) anchors[i][1] = Math.max(anchors[i][1], anchors[i - 1][1]);
  return anchors;
}

function xAt(q) {
  // Strip position (px) of score position q, interpolated between the surrounding notes.
  const A = S.anchors;
  if (q <= A[0][0]) return A[0][1];
  if (q >= A[A.length - 1][0]) return A[A.length - 1][1];
  let lo = 0, hi = A.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (A[mid][0] <= q) lo = mid; else hi = mid;
  }
  const [q0, x0] = A[lo], [q1, x1] = A[hi];
  return x0 + ((q - q0) / (q1 - q0)) * (x1 - x0);
}

// ---- playback loop -------------------------------------------------------------------------

function frame() {
  if (S && S.spans.length) {
    const q = qAt(audio.currentTime);
    if (S.loop.b !== null && !audio.paused && q >= S.loop.b) audio.currentTime = tAt(S.loop.a);
    highlight(q);
    if ($("follow").checked && (!audio.paused || q !== S.lastQ)) follow(q);
    S.lastQ = q;
    $("clock").textContent = clock(audio.currentTime);
    $("barno").textContent = `bar ${Math.max(1, barOf(q) + 1)}`;
  }
  requestAnimationFrame(frame);
}

function highlight(q) {
  const now = new Set();
  for (const n of S.spans) {
    if (n.on > q) break;
    if (q < n.off) now.add(n);
  }
  for (const n of S.active) if (!now.has(n)) n.el.classList.remove("playing");
  for (const n of now) if (!S.active.has(n)) n.el.classList.add("playing");
  S.active = now;
}

function follow(q) {
  if (mode === "line") {
    // Glide continuously: the playing position always sits under the playhead.
    const strip = $("strip");
    strip.scrollLeft = xAt(q) - strip.clientWidth / 2;
    return;
  }
  // Pages: when the music reaches another system, bring that system to the middle of the screen
  // (the same rule handles jumps backwards: loops, clicks, the arrow keys).
  const first = S.active.values().next().value;
  const system = first?.el.closest("g.system");
  if (!system || system === S.system) return;
  S.system = system;
  const header = document.querySelector(".bar").getBoundingClientRect().height;
  const r = system.getBoundingClientRect();
  const middle = header + (window.innerHeight - header) / 2;
  window.scrollBy({ top: (r.top + r.bottom) / 2 - middle, behavior: "smooth" });
}

// ---- controls ------------------------------------------------------------------------------

function togglePlay() { if (S) (audio.paused ? audio.play() : audio.pause()); }

function seekBar(delta) {
  if (!S) return;
  const bar = barOf(qAt(audio.currentTime) + 1e-6) + delta;
  audio.currentTime = Math.max(0, tAt(Math.max(0, bar) * S.sync.beats_per_bar));
}

function setLoop(which) {
  if (!S) return;
  const bpb = S.sync.beats_per_bar, bar = barOf(qAt(audio.currentTime) + 1e-6);
  if (which === "a") {
    S.loop.a = bar * bpb;
    if (S.loop.b === null || S.loop.b <= S.loop.a) S.loop.b = S.loop.a + bpb;
  } else {
    S.loop.b = (bar + 1) * bpb;
    if (S.loop.a === null || S.loop.a >= S.loop.b) S.loop.a = S.loop.b - bpb;
  }
  showLoop();
}

function clearLoop() { if (S) { S.loop = { a: null, b: null }; showLoop(); } }

function showLoop() {
  const { a, b } = S.loop, bpb = S.sync.beats_per_bar;
  const first = a / bpb + 1, last = b / bpb;
  $("looplabel").textContent = a === null ? "" : first === last ? `bar ${first}` : `bars ${first}–${last}`;
  $("loopClear").disabled = a === null;
}

function clock(t) {
  const s = Math.max(0, Math.floor(t));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

$("play").addEventListener("click", togglePlay);
$("restart").addEventListener("click", () => { if (S) audio.currentTime = Math.max(0, tAt(0) - LEAD_IN); });
$("speed").addEventListener("change", (e) => { audio.playbackRate = Number(e.target.value); audio.preservesPitch = true; });
$("loopA").addEventListener("click", () => setLoop("a"));
$("loopB").addEventListener("click", () => setLoop("b"));
$("loopClear").addEventListener("click", clearLoop);
$("song").addEventListener("change", (e) => loadSong(e.target.value));
$("follow").addEventListener("change", () => { if (S) { S.system = null; S.lastQ = null; } });
$("modePages").addEventListener("click", () => setMode("pages"));
$("modeLine").addEventListener("click", () => setMode("line"));
window.addEventListener("resize", () => { if (S && mode === "line") { S.anchors = lineAnchors(); S.lastQ = null; } });
audio.addEventListener("play", () => { $("play").textContent = "⏸"; $("play").setAttribute("aria-label", "Pause"); });
audio.addEventListener("pause", () => { $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play"); });
audio.addEventListener("ratechange", () => { audio.preservesPitch = true; });

$("score").addEventListener("click", (e) => {
  const g = e.target.closest("g.note");
  const n = g && S?.byId.get(g.id);
  if (n) audio.currentTime = tAt(n.on);
});

document.addEventListener("keydown", (e) => {
  if (e.target.matches("select, input")) return;
  const actions = {
    " ": togglePlay, ArrowLeft: () => seekBar(-1), ArrowRight: () => seekBar(1),
    Home: () => $("restart").click(), "[": () => setLoop("a"), "]": () => setLoop("b"), Escape: clearLoop,
    v: () => setMode(mode === "line" ? "pages" : "line"),
  };
  if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
});

loadSongs();
requestAnimationFrame(frame);
