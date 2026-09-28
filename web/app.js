// pianoscribe practice viewer: the score, with the notes lighting up as the original recording plays.
//
// Positions: the recording's time <-> beats (the tracked beat grid, so rubato is followed) <->
// score position q, in quarter notes from the start of bar 1 (Verovio's timemap) <-> ticks, the
// pipeline's grid (12 per beat, 0 = first downbeat), which is how edits name notes.

const $ = (id) => document.getElementById(id);
const audio = $("audio");
const LEAD_IN = 1.0; // seconds of recording before bar 1 when starting from the top
const TICKS = 12; // per beat
const STEP = 3; // a 16th

let S = null; // the loaded song
let mode = "pages";
let editing = false;
try { mode = localStorage.getItem("pianoscribe.mode") === "line" ? "line" : "pages"; } catch {}

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
const tickOf = (q) => Math.round((q - S.sync.shift_beats) * TICKS); // score position -> pipeline tick
const barOf = (q) => Math.floor(q / S.sync.beats_per_bar);

// ---- loading -------------------------------------------------------------------------------

async function loadSongs(select = null) {
  // Start-up: open the song in the address (#slug), or land on the shelf of every score.
  await refreshLibrary();
  const wanted = select ?? decodeURIComponent(location.hash.slice(1));
  if (songs.some((s) => s.slug === wanted)) {
    document.body.classList.remove("landing");
    await loadSong(wanted);
  } else {
    document.body.classList.add("landing");
    history.replaceState(null, "", location.pathname + location.search);
  }
}

// ---- the library: a sidebar of cards, and the shelf (the landing page) -----------------------

let songs = [];
let adding = null; // the song being added, shown as a card until it's ready: {label, message, status}
let prefetched = null; // {slug, view}: the deck under the pointer, fetched before it's clicked
const wide = () => matchMedia("(min-width: 1000px)").matches; // the library pushes the score aside
const libraryOpen = () => document.body.classList.contains("lib-open");
const onShelf = () => document.body.classList.contains("landing");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const img = (slug, page, version) =>
  `<img src="/api/songs/${encodeURIComponent(slug)}/view/page-${page}.svg?v=${version}" alt="" loading="lazy" ` +
  `onload="this.classList.add('ready')">`; // fade in once decoded: big SVG pages take a moment
const facts = (s) => [s.meter && `${s.meter}/4`, s.key, s.duration && clock(s.duration), s.edits && `✎ ${s.edits}`]
  .filter(Boolean).join(" · ");

function setLibrary(open) {
  document.body.classList.toggle("lib-open", open);
  $("libToggle").setAttribute("aria-expanded", String(open));
  $("libToggle").classList.toggle("on", open);
}

// When the library steps aside by itself (☰ and B always toggle it by hand): as soon as there is
// music to read, i.e. a song was picked or starts playing. It doesn't come back on pause (pauses
// are constant when practicing) nor at the end; the shelf is the landing page instead.
function autoLibrary(event) {
  if (event === "play" || event === "pick") setLibrary(false);
}

async function refreshLibrary() {
  songs = await (await fetch("/api/songs")).json();
  renderLibrary();
  renderShelf();
}

function renderLibrary() {
  $("songList").innerHTML = '<li id="addingCard" hidden></li>' + songs.map((s) => {
    const here = s.slug === S?.slug;
    return `<li><button class="card${here ? " current" : ""}" data-slug="${esc(s.slug)}"${here ? ' aria-current="true"' : ""}>
      <span class="thumb">${img(s.slug, 1, s.version)}</span>
      <span class="meta"><span class="title">${esc(s.title)}</span><span class="composer muted">${esc(s.composer || " ")}</span>
        <span class="facts muted">${esc(facts(s))}</span></span>
    </button></li>`;
  }).join("");
  renderAdding();
}

function markCurrent() {
  // Outline the open song's card (without re-rendering: new <img>s would fade in all over again).
  for (const card of $("songList").querySelectorAll(".card[data-slug]")) {
    const here = card.dataset.slug === S?.slug;
    card.classList.toggle("current", here);
    if (here) card.setAttribute("aria-current", "true"); else card.removeAttribute("aria-current");
  }
}

function renderShelf() {
  // Each score as a deck: page 1 in front, the next two behind it (fanned out on hover).
  $("shelfCount").textContent = songs.length ? `${songs.length} score${songs.length > 1 ? "s" : ""}` : "";
  $("shelfEmpty").hidden = songs.length > 0 || !!adding;
  $("decks").innerHTML = '<li id="addingDeck" hidden></li>' + songs.map((s) => `
    <li><button class="deck" data-slug="${esc(s.slug)}">
      <span class="sheets">
        ${s.pages >= 3 ? `<span class="sheet b2">${img(s.slug, 3, s.version)}</span>` : ""}
        ${s.pages >= 2 ? `<span class="sheet b1">${img(s.slug, 2, s.version)}</span>` : ""}
        <span class="sheet front">${img(s.slug, 1, s.version)}</span>
      </span>
      <span class="caption"><span class="title">${esc(s.title)}</span><span class="composer muted">${esc(s.composer || " ")}</span>
        <span class="facts muted">${esc(facts(s))}</span></span>
    </button></li>`).join("");
  renderAdding();
}

function renderAdding() {
  // The song being transcribed, updated in place (re-rendering the lists would reload every page).
  const show = !!adding && adding.status !== "done";
  const mark = adding?.status === "error" ? "✕" : "♪";
  const text = show ? `<span class="title">${esc(adding.label)}</span><span class="facts muted">${esc(adding.message)}</span>` : "";
  for (const [id, html] of [
    ["addingCard", `<div class="card pending" aria-live="polite"><span class="thumb">${mark}</span><span class="meta">${text}</span></div>`],
    ["addingDeck", `<div class="deck pending" aria-live="polite"><span class="sheets"><span class="sheet front">${mark}</span></span>` +
                   `<span class="caption">${text}</span></div>`],
  ]) {
    const li = $(id);
    if (!li) continue;
    li.hidden = !show;
    li.innerHTML = show ? html : "";
  }
}

function prefetch(slug) {
  if (prefetched?.slug !== slug) prefetched = { slug, view: fetchView(slug) };
  return prefetched.view;
}

// ---- moving between the shelf and a score ----------------------------------------------------

const named = []; // elements given a view-transition-name for the current move

function vtName(el, name, cls = "") {
  if (!el) return;
  el.style.viewTransitionName = name;
  el.style.viewTransitionClass = cls;
  named.push(el);
}

function clearNames() {
  for (const el of named.splice(0)) el.style.viewTransitionName = el.style.viewTransitionClass = "";
}

async function transition(update, stagger = []) {
  // Morph from the current screen to the one update() builds: the browser snapshots both and
  // animates each named element from its old box to its new one. Without the API: just swap.
  if (!document.startViewTransition || matchMedia("(prefers-reduced-motion: reduce)").matches) {
    clearNames();
    await update();
    clearNames();
    return;
  }
  // Staggered departures: each song's card leaves a moment after the previous one, not as a block.
  $("vtStagger").textContent = stagger.flatMap((names, i) => names.map((n) =>
    `::view-transition-group(${n}), ::view-transition-old(${n}), ::view-transition-new(${n}) { animation-delay: ${i * 50}ms; }`,
  )).join("\n");
  document.documentElement.classList.add("vt");
  try {
    const t = document.startViewTransition(async () => { clearNames(); await update(); });
    t.ready.catch(() => {}); // skipped (e.g. the tab was hidden): the swap still happens, just without the morph
    await t.finished;
  } finally {
    document.documentElement.classList.remove("vt");
    clearNames();
  }
}

function nameScore() {
  // The score's first pages, or the line: where the chosen deck's sheets fly to (and back from).
  const main = $("score");
  const els = mode === "line" ? [main.querySelector(".stripwrap")] : [...main.querySelectorAll(".page")].slice(0, 3);
  els.forEach((el, i) => vtName(el, i ? `score-page-${i + 1}` : "score-page"));
}

function nameDeck(deck, current) {
  if (current) {
    vtName(deck.querySelector(".front"), "score-page");
    vtName(deck.querySelector(".b1"), "score-page-2");
    vtName(deck.querySelector(".b2"), "score-page-3");
  } else {
    vtName(deck.querySelector(".sheets"), `thumb-${deck.dataset.slug}`, "fly clip");
    vtName(deck.querySelector(".caption"), `meta-${deck.dataset.slug}`, "fly");
  }
}

function nameCard(card) {
  vtName(card.querySelector(".thumb"), `thumb-${card.dataset.slug}`, "fly clip");
  vtName(card.querySelector(".meta"), `meta-${card.dataset.slug}`, "fly");
}

const flyers = (slug) => songs.filter((s) => s.slug !== slug).map((s) => [`thumb-${s.slug}`, `meta-${s.slug}`]);

async function openFromShelf(slug, push = true) {
  // The chosen deck grows into the score, its back pages become pages 2 and 3, the other decks fly
  // into the library's cards; then the library steps aside, showing where the scores went.
  const view = prefetch(slug);
  if (push) history.pushState(null, "", `#${encodeURIComponent(slug)}`);
  for (const deck of $("decks").querySelectorAll(".deck[data-slug]")) nameDeck(deck, deck.dataset.slug === slug);
  await transition(async () => {
    document.body.classList.remove("landing");
    document.body.classList.add("intro");
    setLibrary(true);
    window.scrollTo(0, 0);
    if (slug === S?.slug) { document.title = `${S.sync.title || slug} · pianoscribe`; relayout(); }
    else await loadSong(slug, await view);
    nameScore();
    vtName($("library"), "library"); // slides in over the score, the cards landing in it
    for (const card of $("songList").querySelectorAll(".card[data-slug]")) if (card.dataset.slug !== slug) nameCard(card);
  }, flyers(slug));
  prefetched = null;
  await sleep(500);
  if (document.body.classList.contains("intro")) autoLibrary("pick");
  document.body.classList.remove("intro");
}

async function showShelf() {
  // The way back: the score shrinks into its deck, the library's cards fly back to theirs.
  if (editing) toggleEditing();
  audio.pause();
  prefetched = null;
  const from = S && !onShelf() ? S.slug : null;
  if (from) {
    nameScore();
    if (libraryOpen()) {
      vtName($("library"), "library");
      for (const card of $("songList").querySelectorAll(".card[data-slug]")) if (card.dataset.slug !== from) nameCard(card);
    }
  }
  await transition(() => {
    document.body.classList.add("landing");
    document.body.classList.remove("intro");
    setLibrary(false);
    window.scrollTo(0, 0);
    document.title = "pianoscribe";
    for (const deck of $("decks").querySelectorAll(".deck[data-slug]")) nameDeck(deck, deck.dataset.slug === from);
  }, from ? flyers(from) : []);
}

async function goToShelf() {
  if (S?.pending.length && !confirm("Leave this song? Its unsaved changes will be lost.")) return;
  if (S?.pending.length) await discardEdits();
  history.pushState(null, "", location.pathname + location.search);
  await showShelf();
}

async function openSong(slug) {
  // From anywhere: animated from the shelf, directly from a song.
  if (onShelf()) return openFromShelf(slug);
  if (slug === S?.slug) return;
  if (S?.pending.length && !confirm("Leave this song? Its unsaved changes will be lost.")) return;
  await loadSong(slug);
}

function relayout() {
  // The score's width changed (window resized, library opened or closed): re-measure what follow uses.
  if (!S) return;
  S.system = null;
  S.lastQ = null;
  if (mode === "line") S.anchors = lineAnchors();
}

async function fetchView(slug) {
  // The saved state of a song's viewer files (cache-busted: they change when edits are saved).
  const base = `/api/songs/${encodeURIComponent(slug)}/view/`;
  const v = `?v=${Date.now()}`;
  const [sync, notes] = await Promise.all([
    fetch(base + "sync.json" + v).then((r) => r.json()),
    fetch(base + "notes.json" + v).then((r) => r.json()),
  ]);
  const pages = await Promise.all(
    Array.from({ length: sync.pages }, (_, i) => fetch(`${base}page-${i + 1}.svg${v}`).then((r) => r.text())),
  );
  return { base, sync, notes, pages };
}

async function loadSong(slug, view = null) {
  audio.pause();
  view ??= await fetchView(slug);
  const saved = (await (await fetch(`/api/songs/${encodeURIComponent(slug)}/edits`)).json()).saved.length;
  S = { slug, base: view.base, sync: view.sync, notes: view.notes, svg: { pages: view.pages, line: null },
        spans: [], byId: new Map(), active: new Set(), loop: { a: null, b: null }, system: null,
        anchors: null, lastQ: null, pending: [], history: [], sel: null, saved, previewSeq: 0 };
  $("songTitle").textContent = S.sync.title || slug;
  $("credits").textContent = S.sync.composer || "";
  $("pdf").href = `/api/songs/${encodeURIComponent(slug)}/pdf`;
  document.title = `${S.sync.title || slug} · pianoscribe`;
  history.replaceState(null, "", `#${encodeURIComponent(slug)}`);
  showLoop();
  await render();
  updateEditor();
  markCurrent();
  audio.src = `/api/songs/${encodeURIComponent(slug)}/audio`;
  audio.addEventListener("loadedmetadata", () => { audio.currentTime = Math.max(0, tAt(0) - LEAD_IN); },
                         { once: true });
}

async function reloadSaved() {
  // Back to the saved score (after saving or discarding), keeping the place and the view.
  const view = await fetchView(S.slug);
  Object.assign(S, { sync: view.sync, notes: view.notes, svg: { pages: view.pages, line: null } });
  await keepingPlace(render);
}

// ---- views ---------------------------------------------------------------------------------

async function render() {
  // Draw the score in the current view mode and (re)bind the notes to their SVG elements.
  const main = $("score");
  if (mode === "line") {
    S.svg.line ??= await fetch(S.base + "line.svg?v=" + Date.now()).then((r) => r.text());
    main.className = "line";
    main.innerHTML = `<div class="stripwrap"><div class="strip" id="strip"><div class="strip-inner">` +
                     `<div class="pad"></div>${S.svg.line}<div class="pad"></div></div></div>` +
                     `<div class="playhead"></div></div>`;
  } else {
    main.className = "";
    main.innerHTML = S.svg.pages.map((svg) => `<div class="page">${svg}</div>`).join("");
  }
  S.spans = Object.entries(S.notes)
    .map(([id, [on, off, pitch]]) => ({ id, on, off: Math.max(off, on + 0.05), pitch, el: document.getElementById(id) }))
    .filter((n) => n.el)
    .sort((a, b) => a.on - b.on);
  S.byId = new Map(S.spans.map((n) => [n.id, n]));
  S.active = new Set();
  S.system = null;
  S.lastQ = null;
  S.anchors = mode === "line" ? lineAnchors() : null;
  $("modePages").classList.toggle("on", mode === "pages");
  $("modeLine").classList.toggle("on", mode === "line");
  if (S.sel) reselect();
}

async function keepingPlace(fn) {
  // Re-render without losing the reader's place on screen.
  const strip = $("strip");
  const x = strip?.scrollLeft, y = window.scrollY;
  await fn();
  if (mode === "line" && $("strip") && x != null) $("strip").scrollLeft = x;
  else window.scrollTo(0, y);
}

async function setMode(m) {
  if (m === mode || !S) return;
  mode = m;
  try { localStorage.setItem("pianoscribe.mode", m); } catch {}
  if (m === "line" && S.pending.length) { S.svg.line = null; await runPreview(); } // saved line.svg lacks them
  else await render();
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
  const A = S.anchors;
  if (!A?.length) return 0;
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
    if (strip) strip.scrollLeft = xAt(q) - strip.clientWidth / 2;
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

// ---- correcting the score ------------------------------------------------------------------

// Note names as the score spells them (notation/musicxml.py: spell): the key's own notes, the
// others by their role in the mode, no double sharps or flats.
const NATURAL = [0, 2, 4, 5, 7, 9, 11];
const CHROMATIC = { major: { 1: [0, 1], 3: [2, -1], 6: [3, 1], 8: [5, -1], 10: [6, -1] },
                    minor: { 1: [1, -1], 4: [2, 1], 6: [3, 1], 9: [5, 1], 11: [6, 1] } };
function noteName(p) {
  const { key_sharps: sharps = 0, key_tonic: tonic = 0, key_mode: keyMode = "major" } = S.sync;
  const alters = [0, 0, 0, 0, 0, 0, 0];
  for (const l of (sharps > 0 ? "FCGDAEB" : "BEADGCF").slice(0, Math.abs(sharps))) alters["CDEFGAB".indexOf(l)] = Math.sign(sharps);
  const pc = p % 12, own = (l) => (NATURAL[l] + alters[l] + 12) % 12;
  let letter = [0, 1, 2, 3, 4, 5, 6].find((l) => own(l) === pc), alter;
  if (letter !== undefined) alter = alters[letter];
  else {
    const [degree, shift] = CHROMATIC[keyMode][(pc - tonic + 12) % 12];
    letter = ([0, 1, 2, 3, 4, 5, 6].find((l) => own(l) === tonic) + degree) % 7;
    alter = alters[letter] + shift;
    if (Math.abs(alter) > 1) {
      if (NATURAL.includes(pc)) [letter, alter] = [NATURAL.indexOf(pc), 0];
      else [letter, alter] = sharps > 0 ? [NATURAL.indexOf(pc - 1), 1] : [NATURAL.indexOf((pc + 1) % 12), -1];
    }
  }
  const octave = Math.floor((p - NATURAL[letter] - alter) / 12) - 1;
  return "CDEFGAB"[letter] + (alter > 0 ? "♯" : alter < 0 ? "♭" : "") + octave;
}

function handOf(span) {
  // Staff 1 (top) is the right hand, staff 2 the left.
  const staff = span.el.closest("g.staff");
  const staves = staff ? [...staff.parentNode.querySelectorAll(":scope > g.staff")] : [];
  return staves.indexOf(staff) === 1 ? "L" : "R";
}

function select(span) {
  for (const el of document.querySelectorAll("g.note.selected")) el.classList.remove("selected");
  if (!span) { S.sel = null; updateEditor(); return; }
  span.el.classList.add("selected");
  S.sel = { pitch: span.pitch, tick: tickOf(span.on), start: tickOf(span.on), end: tickOf(span.off),
            hand: handOf(span), q: span.on };
  updateEditor();
}

function reselect() {
  // After a re-render, find the selected note again by what it is (pitch sounding at tick).
  const { pitch, tick } = S.sel;
  const hits = S.spans.filter((n) => n.pitch === pitch && tickOf(n.on) <= tick && tick < Math.max(tickOf(n.off), tickOf(n.on) + 1));
  const span = hits.find((n) => tickOf(n.on) === tick) ?? hits[0];
  if (span) select(span); else { S.sel = null; updateEditor(); }
}

function edit(op, opts = {}) {
  if (!S?.sel) return;
  const sel = S.sel;
  const target = { pitch: sel.pitch, tick: sel.tick };
  let e, next = { ...sel };
  if (op === "pitch") { e = { op, target, delta: opts.delta }; next.pitch = Math.min(108, Math.max(21, sel.pitch + opts.delta)); }
  else if (op === "hand") { if (opts.hand === sel.hand) return; e = { op, target, hand: opts.hand }; next.hand = opts.hand; }
  else if (op === "length") e = { op, target, delta: opts.delta };
  else if (op === "move") { e = { op, target, delta: opts.delta }; next.tick += opts.delta; }
  else if (op === "delete") { e = { op, target }; next = null; }
  else if (op === "add") {
    const pitch = Math.min(108, sel.pitch + 2);
    e = { op, pitch, start: sel.start, end: Math.max(sel.end, sel.start + STEP), hand: sel.hand };
    next = { ...sel, pitch, tick: sel.start };
  }
  S.pending.push(e);
  S.history.push(sel);
  const el = document.querySelector("g.note.selected");
  if (el) el.classList.add("pending"); // instant feedback until the re-engraved preview arrives
  S.sel = next;
  updateEditor();
  schedulePreview();
}

let previewTimer = null;
function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 250);
}

async function runPreview() {
  const seq = ++S.previewSeq;
  busy("Updating preview…");
  try {
    const r = await fetch(`/api/songs/${encodeURIComponent(S.slug)}/preview`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pending: S.pending, line: mode === "line" }),
    });
    if (!r.ok) throw new Error(await r.text());
    const v = await r.json();
    if (seq !== S.previewSeq) return; // a newer edit is already on its way
    Object.assign(S, { sync: v.sync, notes: v.notes, svg: { pages: v.pages, line: v.line } });
    await keepingPlace(render);
    updateEditor();
    busy(v.skipped?.length ? `${v.skipped.length} change(s) no longer match a note` : "");
  } catch (err) {
    if (seq === S.previewSeq) busy(`Preview failed: ${err.message.slice(0, 120)}`);
  }
}

async function saveEdits() {
  if (!S?.pending.length) return;
  busy("Saving… rebuilding the score and PDF");
  const r = await fetch(`/api/songs/${encodeURIComponent(S.slug)}/edits`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pending: S.pending }),
  });
  if (!r.ok) { busy("Saving failed"); return; }
  const res = await r.json();
  S.pending = []; S.history = []; S.saved = res.saved;
  await reloadSaved();
  updateEditor();
  refreshLibrary();
  busy(`Saved · rebuilt in ${res.seconds}s`);
}

async function discardEdits() {
  if (!S?.pending.length) return;
  S.pending = []; S.history = [];
  await reloadSaved();
  updateEditor();
  busy("");
}

async function undoEdit() {
  if (!S?.pending.length) return;
  S.pending.pop();
  S.sel = S.history.pop() ?? S.sel;
  updateEditor();
  if (S.pending.length) schedulePreview(); else { await reloadSaved(); busy(""); }
}

async function revertAll() {
  if (!S?.saved || !confirm(`Remove all ${S.saved} saved corrections to this score?`)) return;
  busy("Reverting… rebuilding the score and PDF");
  await fetch(`/api/songs/${encodeURIComponent(S.slug)}/edits`, { method: "DELETE" });
  S.pending = []; S.history = []; S.saved = 0;
  await reloadSaved();
  updateEditor();
  busy("All corrections removed");
}

// Score-wide settings: key, transpose, tempo mark, beat factor, meter, barline shift.
const KEYS = [["C", 0, "major"], ["G", 7, "major"], ["D", 2, "major"], ["A", 9, "major"], ["E", 4, "major"],
              ["B", 11, "major"], ["G♭", 6, "major"], ["D♭", 1, "major"], ["A♭", 8, "major"], ["E♭", 3, "major"],
              ["B♭", 10, "major"], ["F", 5, "major"], ["A", 9, "minor"], ["E", 4, "minor"], ["B", 11, "minor"],
              ["F♯", 6, "minor"], ["C♯", 1, "minor"], ["G♯", 8, "minor"], ["E♭", 3, "minor"], ["B♭", 10, "minor"],
              ["F", 5, "minor"], ["C", 0, "minor"], ["G", 7, "minor"], ["D", 2, "minor"]];
let tab = "note";

function setting(e) {
  if (!S) return;
  S.pending.push(e);
  S.history.push(S.sel);
  updateEditor();
  schedulePreview();
}

function showSettings() {
  const st = S.sync.settings || {};
  $("setKey").innerHTML = `<option value="auto">Auto (${esc(st.key ? "estimated" : S.sync.key || "estimated")})</option>` +
    KEYS.map(([n, t, m]) => `<option value="${t}:${m}">${n} ${m}</option>`).join("");
  $("setKey").value = st.key ? `${st.key[0]}:${st.key[1]}` : "auto";
  $("setTranspose").textContent = (st.transpose > 0 ? "+" : "") + (st.transpose || 0);
  $("setBpm").value = st.bpm_mark ?? Math.round(S.sync.bpm);
  $("setMeter").value = String(S.sync.beats_per_bar);
  for (const b of $("setBeat").children) b.classList.toggle("on", Number(b.dataset.factor) === (st.beat ?? 1));
  $("setHint").textContent = st.transpose ? "Transposed: the recording still plays in the original key." : "";
}

function busy(text) { $("edBusy").textContent = text; }

function updateEditor() {
  $("editor").hidden = !editing;
  document.body.classList.toggle("editing", editing);
  $("editToggle").classList.toggle("on", editing);
  if (S?.pending.length) $("editToggle").dataset.pending = S.pending.length;
  else delete $("editToggle").dataset.pending;
  if (!S) return;
  $("tabNote").classList.toggle("on", tab === "note");
  $("tabScore").classList.toggle("on", tab === "score");
  $("scoreTools").hidden = tab !== "score";
  document.querySelector(".ed-tools").hidden = tab !== "note";
  if (tab === "score") showSettings();
  const sel = S.sel;
  const bpb = S.sync.beats_per_bar;
  $("edNote").textContent = tab === "score" ? `${S.sync.key} · ${bpb}/4 · ♩ = ${(S.sync.settings || {}).bpm_mark ?? Math.round(S.sync.bpm)}`
    : sel ? `${noteName(sel.pitch)} · ${sel.hand === "L" ? "left" : "right"} hand · bar ${Math.floor(sel.q / bpb) + 1}, beat ${Math.floor((sel.q % bpb) * 4) / 4 + 1}`
    : "Click a note to select it";
  for (const b of document.querySelectorAll(".ed-tools button")) b.disabled = !sel;
  for (const b of document.querySelectorAll('.ed-tools [data-op="hand"]')) b.classList.toggle("on", !!sel && b.dataset.hand === sel.hand);
  const p = S.pending.length;
  $("edCount").textContent = (p ? `${p} unsaved change${p > 1 ? "s" : ""} (preview)` : "No unsaved changes") +
                             (S.saved ? ` · ${S.saved} saved` : "");
  $("edUndo").disabled = $("edDiscard").disabled = $("edSave").disabled = !p;
  $("edRevert").disabled = !S.saved;
}

function toggleEditing() {
  editing = !editing;
  if (!editing && S) select(null);
  updateEditor();
}

// ---- adding a song -------------------------------------------------------------------------

const STEPS = [["fetch", "Download"], ["transcribe", "Listen for notes"], ["beats", "Find the beat"],
               ["score", "Write the score"], ["done", "Ready"]];
let addChoice = null; // {url, title} or {file}
let jobPoll = null;

function openAdd() {
  addChoice = null;
  $("addResults").innerHTML = "";
  $("addDetails").hidden = true;
  $("addProgress").hidden = true;
  $("addFile").value = "";
  $("addDialog").showModal();
  $("addQuery").focus();
}

function guessCredits(raw) {
  // "Artist - Song | Piano cover" -> {title: Song, composer: Artist}; ⇅ fixes the other order.
  const head = (raw || "").split(/[|(\[]/)[0].trim();
  const parts = head.split(/\s+[-–—]\s+/);
  return parts.length >= 2 ? { title: parts.slice(1).join(" - "), composer: parts[0] } : { title: head, composer: "" };
}

async function searchAdd() {
  const q = $("addQuery").value.trim();
  if (!q) return;
  if (/^https?:\/\//.test(q)) {
    $("addResults").innerHTML = '<li class="m">Looking up the link…</li>';
    const info = await (await fetch(`/api/probe?url=${encodeURIComponent(q)}`)).json();
    $("addResults").innerHTML = "";
    chooseAdd({ url: info.url || q, title: info.title, channel: info.channel });
    return;
  }
  $("addResults").innerHTML = '<li class="m">Searching…</li>';
  const results = await (await fetch(`/api/search?q=${encodeURIComponent(q)}`)).json();
  $("addResults").innerHTML = results.map((r, i) =>
    `<li data-i="${i}"><img src="${esc(r.thumbnail)}" alt="" loading="lazy">` +
    `<div><div class="t">${esc(r.title)}</div><div class="m">${esc(r.channel || "")} · ${clock(r.duration || 0)}</div></div></li>`,
  ).join("") || '<li class="m">No results</li>';
  for (const li of $("addResults").querySelectorAll("li[data-i]")) {
    li.addEventListener("click", () => {
      for (const x of $("addResults").children) x.classList.remove("on");
      li.classList.add("on");
      chooseAdd(results[Number(li.dataset.i)]);
    });
  }
}

function chooseAdd(choice) {
  addChoice = choice;
  const g = guessCredits(choice.title || choice.file?.name?.replace(/\.[^.]+$/, ""));
  $("addChosen").textContent = choice.title || choice.file?.name || "";
  $("addTitle").value = g.title;
  $("addComposer").value = g.composer;
  $("addDetails").hidden = false;
  $("addTitle").focus();
}

async function startAdd() {
  if (!addChoice) return;
  const title = $("addTitle").value.trim(), composer = $("addComposer").value.trim();
  let r;
  if (addChoice.file) {
    const form = new FormData();
    form.append("file", addChoice.file);
    form.append("title", title);
    form.append("composer", composer);
    r = await fetch("/api/upload", { method: "POST", body: form });
  } else {
    r = await fetch("/api/add", { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({ source: addChoice.url, title, composer }) });
  }
  if (!r.ok) { $("addMsg").textContent = `Could not start: ${await r.text()}`; $("addProgress").hidden = false; return; }
  const job = await r.json();
  $("addDetails").hidden = true;
  $("addProgress").hidden = false;
  watchJob(job.id, title || addChoice.title || "song");
}

function watchJob(id, label) {
  clearInterval(jobPoll);
  const tick = async () => {
    const job = await (await fetch(`/api/jobs/${id}`)).json();
    const at = STEPS.findIndex(([k]) => k === job.step);
    $("addSteps").innerHTML = STEPS.map(([, name], i) =>
      `<li class="${job.status === "done" || i < at ? "done" : i === at ? "now" : ""}">${name}</li>`).join("");
    $("addMsg").textContent = job.status === "error" ? `Something went wrong: ${job.error}` :
                              job.status === "queued" ? "Waiting for the previous song to finish…" : `${job.message}…`;
    $("jobStatus").textContent = job.status === "running" || job.status === "queued" ? `♪ ${label}: ${job.message}` :
                                 job.status === "done" ? `✓ ${label} is ready` : job.status === "error" ? `✕ ${label} failed` : "";
    adding = { label, status: job.status, message: job.status === "queued" ? "waiting for the previous song…" :
               job.status === "error" ? "failed" : `${job.message}…` };
    if (job.status === "done") {
      clearInterval(jobPoll);
      $("addMsg").textContent = "Ready!";
      adding = null;
      await refreshLibrary();
      // Open the new song if the dialog is still up; otherwise just add its card (someone may be practicing).
      if ($("addDialog").open) {
        await sleep(800);
        $("addDialog").close();
        await openSong(job.result);
      }
    } else {
      if (job.status === "error") clearInterval(jobPoll);
      $("shelfEmpty").hidden = true;
      renderAdding();
    }
  };
  tick();
  jobPoll = setInterval(tick, 1500);
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

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

$("play").addEventListener("click", togglePlay);
$("restart").addEventListener("click", () => { if (S) audio.currentTime = Math.max(0, tAt(0) - LEAD_IN); });
$("speed").addEventListener("change", (e) => { audio.playbackRate = Number(e.target.value); audio.preservesPitch = true; });
$("loopA").addEventListener("click", () => setLoop("a"));
$("loopB").addEventListener("click", () => setLoop("b"));
$("loopClear").addEventListener("click", clearLoop);
$("libToggle").addEventListener("click", () => setLibrary(!libraryOpen()));
$("songList").addEventListener("click", async (e) => {
  const card = e.target.closest(".card[data-slug]");
  if (!card) return;
  await openSong(card.dataset.slug);
  autoLibrary("pick");
});
$("decks").addEventListener("click", (e) => {
  const deck = e.target.closest(".deck[data-slug]");
  if (deck) openFromShelf(deck.dataset.slug);
});
for (const type of ["pointerover", "focusin"]) {
  $("decks").addEventListener(type, (e) => { const deck = e.target.closest(".deck[data-slug]"); if (deck) prefetch(deck.dataset.slug); });
}
$("toShelf").addEventListener("click", goToShelf);
$("shelfAdd").addEventListener("click", openAdd);
window.addEventListener("popstate", async () => {
  // Back and forward between the shelf and songs.
  const slug = decodeURIComponent(location.hash.slice(1));
  if (S?.pending.length && slug !== S.slug) {
    if (!confirm("Leave this song? Its unsaved changes will be lost.")) { history.pushState(null, "", `#${encodeURIComponent(S.slug)}`); return; }
    await discardEdits();
  }
  if (!songs.some((s) => s.slug === slug)) { if (!onShelf()) await showShelf(); }
  else if (onShelf()) await openFromShelf(slug, false);
  else if (slug !== S?.slug) await loadSong(slug);
});
$("score").addEventListener("transitionend", (e) => { if (e.target === $("score") && e.propertyName === "margin-left") relayout(); });
new ResizeObserver(([entry]) => document.documentElement.style.setProperty("--bar-h", `${entry.target.offsetHeight}px`))
  .observe(document.querySelector(".bar"));
$("follow").addEventListener("change", () => { if (S) { S.system = null; S.lastQ = null; } });
$("modePages").addEventListener("click", () => setMode("pages"));
$("modeLine").addEventListener("click", () => setMode("line"));
$("editToggle").addEventListener("click", toggleEditing);
$("addSong").addEventListener("click", openAdd);
$("addGo").addEventListener("click", searchAdd);
$("addQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); searchAdd(); } });
$("addFile").addEventListener("change", (e) => { if (e.target.files[0]) chooseAdd({ file: e.target.files[0] }); });
$("addSwap").addEventListener("click", () => { const t = $("addTitle").value; $("addTitle").value = $("addComposer").value; $("addComposer").value = t; });
$("addStart").addEventListener("click", startAdd);
$("addClose").addEventListener("click", () => $("addDialog").close());
$("edUndo").addEventListener("click", undoEdit);
$("edDiscard").addEventListener("click", discardEdits);
$("edSave").addEventListener("click", saveEdits);
$("edRevert").addEventListener("click", revertAll);
$("tabNote").addEventListener("click", () => { tab = "note"; updateEditor(); });
$("tabScore").addEventListener("click", () => { tab = "score"; updateEditor(); });
$("setKey").addEventListener("change", (e) => {
  const [tonic, mode] = e.target.value.split(":");
  setting(e.target.value === "auto" ? { op: "key", auto: true } : { op: "key", tonic: Number(tonic), mode });
});
$("setBpm").addEventListener("change", (e) => setting({ op: "tempo", bpm: Number(e.target.value) || null }));
$("setMeter").addEventListener("change", (e) => setting({ op: "meter", beats: Number(e.target.value) }));
for (const b of $("setBeat").children) b.addEventListener("click", () => setting({ op: "beat", factor: Number(b.dataset.factor) }));
for (const b of document.querySelectorAll("[data-set]")) {
  b.addEventListener("click", () => {
    const d = Number(b.dataset.delta), st = S?.sync.settings || {};
    setting(b.dataset.set === "transpose" ? { op: "transpose", semitones: d } : { op: "downbeat", shift: (st.downbeat || 0) + d });
  });
}
for (const b of document.querySelectorAll(".ed-tools button")) {
  b.addEventListener("click", () => edit(b.dataset.op, { delta: Number(b.dataset.delta), hand: b.dataset.hand }));
}
window.addEventListener("resize", relayout);
window.addEventListener("beforeunload", (e) => { if (S?.pending.length) e.preventDefault(); });
audio.addEventListener("play", () => { $("play").textContent = "⏸"; $("play").setAttribute("aria-label", "Pause"); autoLibrary("play"); });
audio.addEventListener("pause", () => { $("play").textContent = "▶"; $("play").setAttribute("aria-label", "Play"); autoLibrary("pause"); });
audio.addEventListener("ratechange", () => { audio.preservesPitch = true; });

$("score").addEventListener("click", (e) => {
  const g = e.target.closest("g.note");
  const n = g && S?.byId.get(g.id);
  if (!n) return;
  if (editing) select(n); else audio.currentTime = tAt(n.on);
});

document.addEventListener("keydown", (e) => {
  if ((e.target instanceof Element && e.target.matches("select, input, textarea")) || $("addDialog").open || onShelf()) return;
  const mod = e.metaKey || e.ctrlKey;
  if (editing && mod && e.key.toLowerCase() === "z") { e.preventDefault(); undoEdit(); return; }
  if (editing && mod && e.key.toLowerCase() === "s") { e.preventDefault(); saveEdits(); return; }
  if (mod) return;
  const editKeys = editing && S?.sel ? {
    ArrowUp: () => edit("pitch", { delta: e.shiftKey ? 12 : 1 }), ArrowDown: () => edit("pitch", { delta: e.shiftKey ? -12 : -1 }),
    ArrowLeft: () => edit("move", { delta: -STEP }), ArrowRight: () => edit("move", { delta: STEP }),
    l: () => edit("hand", { hand: "L" }), r: () => edit("hand", { hand: "R" }),
    "+": () => edit("length", { delta: STEP }), "=": () => edit("length", { delta: STEP }), "-": () => edit("length", { delta: -STEP }),
    Backspace: () => edit("delete"), Delete: () => edit("delete"), n: () => edit("add"), Escape: () => select(null),
  } : {};
  const actions = {
    " ": togglePlay, ArrowLeft: () => seekBar(-1), ArrowRight: () => seekBar(1),
    Home: () => $("restart").click(), "[": () => setLoop("a"), "]": () => setLoop("b"), Escape: clearLoop,
    v: () => setMode(mode === "line" ? "pages" : "line"), e: toggleEditing, b: () => setLibrary(!libraryOpen()),
    ...editKeys,
  };
  if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
});

loadSongs();
requestAnimationFrame(frame);
