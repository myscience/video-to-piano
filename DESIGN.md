# pianoscribe: design

Turn a solo-piano recording (a YouTube tutorial, a cover, a local file) into sheet music you can
practice from: a well-engraved PDF, plus a local web viewer with a cursor that follows the
original recording.

Personal-use tool. Scope: **solo piano audio only** (no band/mix separation, for now).

## Pipeline

```
 source ──► audio.wav ──► A. PERCEPTION ──► B. NOTATION ──────────────────► C. ENGRAVING
 (yt-dlp,                  note events       beats & meter (from audio)     score.musicxml ─► PDF
  local file)              notes/*.mid       quantize onsets to beat grid                  └► web viewer
                           (≈ MIDI)          hand split, voices, key/spelling
```

| Stage | Difficulty | Status |
|---|---|---|
| A. Audio → note events | Largely solved for solo piano (~95% note F1, see results below) | ✅ working |
| B. Note events → score | **The hard part**: open research ("performance-to-score") | next |
| C. MusicXML → PDF/viewer | Tooling exists (LilyPond, Verovio) | planned |

## Principles

1. **Note events are the core data model.** A transcription is a flat list of independent
   `NoteEvent(pitch, onset, offset, velocity, hand?)`. Chords, voices and hands are *notation
   decisions*, made in stage B. (The old codebase modelled each hand as one chord at a time,
   so held notes got chopped into fragments under moving melodies.)
2. **Quantize positions, not durations.** Snap each onset's *absolute* time to a beat grid and
   derive durations as differences of snapped positions. Rounding each duration on its own
   accumulates error until barlines drift (the old codebase's main failure).
3. **Beats come from the audio, not a fixed BPM.** An audio beat tracker gives the real beat
   times (tempo drift, rubato); the grid is subdivisions of *those* beats.
4. **Every stage writes to `library/<song>/`.** Any stage can be re-run alone, every
   intermediate is inspectable (open `notes/*.mid` in any DAW), and stage B can be developed
   against MIDI files alone.
5. **MusicXML is the hub.** Aim for a great *draft*: anything the pipeline gets wrong is a few
   minutes of fixing in MuseScore. PDF and viewer both render from MusicXML.
6. **Measure, don't eyeball.** `pianoscribe eval` scores every transcription against a
   reference, so each change to the pipeline gets a number.

## Layout

```
src/pianoscribe/
  cli.py                 # pianoscribe fetch | import | transcribe | merge | beats | score | truth | eval
  library.py             # Song: every per-song path, in one place
  sources/               # yt-dlp download, ffmpeg decode -> mono 44.1 kHz audio.wav
  transcribe/            # NoteEvent/Transcription + pluggable backends, ensemble merge
  rhythm/                # beats and bars, quantization
  notation/              # hands, voices, clefs, edits, our own MusicXML writer
  render/                # MusicXML -> PDF (LilyPond)
  eval/                  # Synthesia-video answer key, mir_eval metrics, piano-roll plots
  server/                # planned: viewer backend
web/                     # planned: viewer
library/<song>/          # gitignored
  <song>.pdf             #   the final score
  source/                #   meta.json, video.mp4 (optional), original.<ext>, audio.wav
  notes/                 #   <backend>.mid: transkun, bytedance, ensemble
  rhythm/                #   activations.npz, beats.json
  truth/                 #   notes.mid, bars.json: answer key from a Synthesia video
  score/                 #   score.musicxml, score.ly, score-page<N>.png
  eval/                  #   roll.png
models/                  # gitignored: downloaded checkpoints
```

## Stage A: transcription backends

Both sit behind the `Transcriber` protocol (`transcribe(audio) -> Transcription`):

- **transkun** (Yan & Duan; transformer + neural semi-CRF). Weights ship inside the pip package.
- **bytedance** (Kong et al., high-resolution piano transcription). Checkpoint (~165 MB):
  `models/bytedance/note_F1=0.9677_pedal_F1=0.9186.pth` from
  <https://zenodo.org/record/4034264>.

Both also output sustain-pedal intervals (stored as MIDI CC64).

## Evaluation: Synthesia videos as ground truth

A Synthesia tutorial video is its source MIDI drawn as pixels, and its audio (fetched separately
with yt-dlp) shares the same timeline. So the video is an **answer key** for the audio.
`pianoscribe truth <song>` extracts it (`eval/synthesia.py`):

- The falling-bars area is a tape scrolling at constant `v` px/frame. A pixel at row `y` in
  frame `f` hits the keyboard at time `(f + (y_line − y)/v) / fps`. Stitching a band of rows
  from every frame rebuilds the tape at **1 px ≈ 2.6 ms** resolution (vs 33 ms per frame), fine
  enough to split repeated notes, whose gaps are only a few px.
- Keyboard geometry (hit line, 78 keys D1–G7) comes from a per-pixel median over many frames.
  The median gives an idle keyboard, even though no single frame is idle.
- Bar color gives the **hand** for free (blue = left, green = right), which will be the answer
  key for stage B's hand split.

`pianoscribe eval <song>` finds the global video↔audio lag (+53..58 ms here) and octave, then
reports mir_eval note metrics (onset ±50 ms; "+off" also requires the offset within 20%).

### Results: "exile" (Theory Notes tutorial, 297 s, 2553 reference notes)

| backend | P | R | F1 | F1 + offsets | speed (M1 Pro, MPS) |
|---|---|---|---|---|---|
| transkun | 98.4% | 93.2% | **95.7%** | 58.7% | 8× realtime |
| bytedance | 97.1% | 90.5% | 93.7% | 79.4% | 1.3× realtime |
| **ensemble** (`merge`) | 98.4% | 93.2% | **95.7%** | **80.3%** | both |

- transkun has the best onsets and is 6× faster. ByteDance is far better at note *ends*,
  which decide notated durations.
- **`pianoscribe merge`** takes transkun as the backbone (it decides which notes exist) and
  borrows ByteDance's note ends. Two invariants make it safe: a borrowed end is capped at the
  next strike of the same key (MIDI can't store same-key overlaps; they get mis-paired on
  reload), and it is only used if the note still lasts ≥ 30 ms.
- The remaining misses are mostly fast repeated chords and very quiet notes.

## Stage B.1: beats and bars

`pianoscribe beats <song>` → `beats.json` (beat + downbeat times; activations cached in
`activations.npz`).

- **Activations** from Beat This! (Foscarin et al. 2024), 50 frames/s. Checkpoint (~81 MB):
  `models/beat_this/final0.ckpt` from
  <https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt>.
- **Beats:** a dynamic-programming decoder (Ellis 2007) maximizing
  `Σ activation at beats − tightness · Σ log(interval / period)²`, with the period = median gap of
  Beat This!'s own beats. The model's peak picking is locally precise (10–25 ms) but has no
  tempo model: on 'exile' it locked onto off-beats for 30 s, dropped beats in the sparse intro
  and burst into double time. The DP rules all three out.
- **Bars:** downbeats are `beats[phase::beats_per_bar]`. Phase = the one with the highest *mean*
  downbeat activation, and meter (3 vs 4) = the one whose best phase scores highest. The margin
  over the runner-up phase is printed as a confidence (the usual confusion is the half-bar, beat 3
  in 4/4).
- **Answer key:** Synthesia videos draw faint full-width bar lines that scroll with the notes
  (`truth/bars.json`). They show up as a lift of the *median* brightness across the width, which
  note bars never cause.

| 'exile' (83 bars, 4/4, 74 BPM) | beat F | downbeat F |
|---|---|---|
| Beat This! peak picking | 84.3% | 69.2% (it guessed 2 beats/bar) |
| DP, tightness 30 / 100 | 91.9% / 90.7% | 67.9% / 67.9% |
| **DP, tightness 300** + phase | **100%** | **100%** |

- One slipped beat shifts the global bar phase for the rest of the song: at tightness 30–100
  beats are still ~91% right but downbeats collapse to 68%. Per-bar phase decoding would fix
  that failure mode.
- Caveat: 'exile' is a constant-tempo MIDI rendering, so the stiffest tightness always wins
  there. Re-check on a rubato recording before trusting the default.

## Stage B.2: quantization

`rhythm/quantize.py`: seconds → continuous beat positions (`to_beats`, linear between tracked
beats) → integer **ticks**, 12 per beat (holds 16ths = 3, 8th triplets = 4, 16th triplets = 2).
Every start and end is snapped *independently* (positions, not durations), to a 16th grid by
default. `pianoscribe eval` compares each quantized note with the video's (both on their own
grids, lined up at the video's first bar):

| 'exile', ensemble notes | start exact | length exact | too short | too long |
|---|---|---|---|---|
| 16th grid, independent snapping | **99.7%** | 84.2% | 5.8% | 10.0% |

- Starts are essentially solved. Note **ends** are the noisy part: no bias overall (median
  +6 ms), but long notes come out ~0.2 beats early (the sound decays before the key is released):
  89% exact for notes < ⅓ beat, 35% for notes > 1 beat.
- 99% of the reference's note ends coincide with some other note's start. But snapping ends to
  the nearest start of *any* note gains little (85.4%): in continuous 16ths there is a start
  everywhere. Even the next start in the *same hand* (oracle hands from the video) reaches only
  87.3%, because a hand holds several voices. **Written lengths are a per-voice notation
  decision**, so they are deferred to score building, after hands and voices.

## Stage B.3: hands

`notation/hands.py`. Audio has no hand information; the video's bar colors are the answer key.
On 'exile' the hands overlap by more than an octave (left up to F#4, right down to D#3).

| 'exile', ensemble notes | correct hand |
|---|---|
| fixed split at middle C | 90.9% |
| greedy (`assign_hands`): nearest hand center, then span ≤ octave, then ≤ 5 notes per hand | 94.9% |
| **DP (`decode_hands`)**: best path over both hands' positions | **96.1%** (ByteDance notes: 96.8%) |

- Both use the same physical rules, measured on the answer key: 352 right-hand chords span
  exactly an octave and only 3 go wider (a limit of 11 semitones collapses to 80.9%).
- Greedy errors come in **runs** (75% within a beat of another): a right-hand dip drags the
  left hand's running center up. The DP judges each choice by what it costs later; its key
  ingredient is a **free reach** of a fifth (a hand in position covers five keys without moving):
  free 0 → 95.1%, 5 → 96.1%, 12 → 94.7%. Anchoring hands at their inner edge was worse.
- Part of the remaining error is the answer key: the tutorial's **audio and on-screen MIDI
  differ slightly**. Both models independently hear notes the video doesn't draw (28 of
  transkun's 40 extras; A#5: 83 heard by each model vs 74 drawn), e.g. an A#5 doubling a
  right-hand A#3–C#4–A#4 chord. For the chord as heard, splitting it between the hands is right.
  On chords where video and audio agree, the DP is at 96.6%.

## Stage B.4 + C: the score

`pianoscribe score <song>` → `score.musicxml` → `score.pdf` (via `musicxml2ly` + LilyPond;
`--png` for page previews). First version, one voice per hand:

- **Song span:** notes are split at silences ≥ 1 s; the longest part is the song, extended over
  neighbours whose notes fit its key (≥ 85% in scale). Drops a tutorial's intro/outro jingles
  (on 'exile': 5.4 s … 277.8 s).
- **Key and spelling:** Krumhansl key estimate (6+ accidentals → the flat-side key: G♭, not F♯);
  scale notes take the key's names (B♭, not A♯), chromatic notes lean like the key signature.
- **Written lengths** (`written_end`): a chord lasts until the hand's next chord; a gap becomes a
  rest only if it's a clear silence. Absorbed when the pedal is down, when it's ≤ an eighth, or
  when it's shorter than half the chord. Readability over fidelity, as arrangers do. The
  half-the-chord rule alone left 29 sixteenth rests (a 16th note, then a 16th gap); the eighth
  floor removes them all, keeping 3 + 7 real rests.
- **Clefs** (`choose_clefs`): a DP over the bars of each staff minimizing the exact ledger-line
  count of every note, plus 8 per clef change and 1 per bar away from the staff's home clef. The
  first bar's clef is free (a low opening *starts* in bass clef instead of switching after one
  bar). A change never lands mid-sustain: when a note is tied across that barline (48 of 84
  right-hand bars begin with a tied note, pop syncopation), it moves back to just before the
  tied note. On 'exile': right hand 1 change (bass for the opening, treble from bar 5), left
  hand none. A switch cost of 4 would add 4 changes to save only 20 ledger lines in the piece.
- **Voices by role, not by duration.** Measured first: notes we'd call "held under the next
  chord" match the arranger's held notes only ~25% of the time (audio can't tell a held key
  from a late release), so voices can't come from note lengths. Instead each hand splits by
  *role* from starts and pitches (99.7% and ~96% reliable): right hand = melody (top note of
  each chord) over accompaniment, left hand = bass (bottom note) under the rest. Each voice runs
  legato to its own next note, with the gap rule.
- **Voice notes stay playable.** Legato inside a voice bridges at most one beat of silence, and
  a held note ends as soon as the same hand plays beyond an octave from it (the hand-split's
  reach). Before this, left-hand notes were stretched for bars over arpeggios (13 of 13 long
  upper-voice notes came from stretching, 9 while the hand played out of reach), drawing long
  tie arcs across other notes.
- **Two voices only where they're worth it**, per bar: both roles play, their rhythms diverge,
  and the sustain they show (that one voice would cut) is ≥ 1 beat and ≥ ½ beat per *visible*
  rest. The secondary voice (right-hand accompaniment, left-hand notes above the bass) hides
  its rests (`print-object="no"`: spacers in LilyPond and Verovio); melody and bass keep theirs.
  A note crossing a barline keeps its bars in the same mode.
- On 'exile': 17 right-hand two-voice bars (melody over held accompaniment), left hand one
  voice throughout; 6 + 8 visible rests in the whole piece; 4 viewer pages instead of 7.
  Earlier attempts: any rhythm difference → 67 + 77 bars with whole-bar rests; bass = lowest
  note *of the beat* (to split arpeggios) → 152 left-hand rests.
- Known limits: note lengths from audio are only 84% exact, 2/4 vs 4/4 and 6/8 aren't
  distinguished, no pickup bars.

## The practice viewer

`pianoscribe serve` → <http://localhost:8765> (`--host 0.0.0.0` to open it from an iPad on the
same Wi-Fi). `pianoscribe score` also writes `score/view/`: SVG pages and a note timemap from
Verovio (rendered once on the server: no WASM in the browser), plus `sync.json`.

- **Sync:** recording time ↔ beat position (the tracked beats, so the cursor follows the real
  performance, rubato included) ↔ score position in quarter notes (Verovio's `qstamp`; 0 = bar 1,
  beat 1, via `shift_beats`). Checked end to end: every score position maps to a heard note,
  median 6 ms, 99% within 26 ms.
- **Two views.** *Pages*: when the music reaches another system, it scrolls to the middle of the
  screen. *Line*: the whole piece as one endless system (`line.svg`, a re-layout of the same
  Verovio document, so note ids match) gliding right to left under a fixed playhead; the
  position is interpolated between the notes around the current beat, so it moves smoothly
  (measured: the playing notes sit within 1 px of the playhead). `v` switches.
- **Practice:** notes light up (mediumorchid) as they sound, click a note to jump there, loop
  bars (A/B, `[` `]`), slow down without pitch change (50–100%), ←/→ one bar, Space play/pause.
- Plain HTML/CSS/JS in `web/` (no build step), FastAPI in `server/`. The audio is the original
  download (served with Range support, so seeking works).
- The viewer's engraving is Verovio's and the PDF's is LilyPond's: same notes, slightly
  different layout.

## Adding songs

`pianoscribe add <url-or-file> [--title --composer]` runs everything (fetch → both transcribers
→ merge → beats → score) through `pipeline.py`, the same functions the CLI steps, the viewer and
the edit rebuilds use. In the viewer, **＋ Add** searches YouTube (yt-dlp metadata only), takes a
pasted link, or uploads a file; you confirm title and composer (⇅ swaps them: a video title can
be "artist - song" or "song - artist", and explicit credits are saved in `meta.json` so they
win over guesses), then a background job (one at a time) reports each step until the song opens.

## Correcting a score ("great draft + easy correction")

Edits never touch the pipeline's outputs: they live in `score/edits.json` and are replayed on the
quantized notes at every build (`notation/edits.py`), so re-running any stage keeps them.

- Each edit names its note by `{pitch, tick}`: the note of that pitch *sounding* at that tick,
  so clicking any part of a tied note finds the whole note, and edits compose in sequence.
  Ops are relative (pitch ±semitones, length/move ±16ths, hand, delete, add a note).
- **A hand correction steers the hand decoder** instead of just overriding one note: only splits
  agreeing with it are allowed, so the best path re-plans around it (neighbours in the same run
  follow). A length edit is kept exactly (`locked`): no legato, gap or reach rule touches it.
- In the viewer, **✎ Edit (E)**: click a note, then ▲▼ (⇧ octave), L/R, −/+ length, ◀▶ move,
  ＋ note, delete, all on the keyboard too. The note is marked pending instantly and the
  re-engraved preview (saved + pending edits, rendered but not written) replaces the page in
  ~0.6 s with the selection following the note. Undo (⌘Z), Discard, Save (⌘S: append to
  `edits.json`, rebuild MusicXML, PDF and viewer, ~2.5 s), Revert all.
- **Score-wide settings** (the editor's *Score* tab) are edits too, folded by `settings()` and
  applied *before* quantization, so every note edit stays attached to the right note:
  - *key*: override the estimated key signature (spelling only, same pitches) or back to auto;
  - *transpose* ±semitones: moves every note (in sequence with the note edits, so earlier edits
    still find their notes at the old pitch) and re-estimates the key; the recording is not
    pitch-shifted, the viewer says so;
  - *tempo mark*: only the printed ♩ = N (the cursor follows the tracked beats regardless);
  - *beat ½× / 2×*: fixes the tracker's classic octave errors by keeping every other beat
    (starting from the downbeat's parity, so bars stay aligned) or inserting midpoints;
  - *meter* 2/3/4 and *barlines ◀▶*: re-slice the same beats into bars (`rhythm.beats.transform`),
    the fix for a wrong downbeat or a 3-vs-4 mistake. Ticks are beat-relative, so the grid
    transform happens first and note edits made afterwards target the new grid.

## Second song: "Never Gonna Give You Up" (PianoX, a filmed pianist)

- Transcribed without changes: 1,574 notes, 143–277 pedal presses (a real player, versus the
  MIDI-rendered 'exile'). Beats: 115.4 BPM, 4/4, bar phase margin 0.67. No bar-line answer key
  here, so the grid was checked by **grid fit**: with the DP, 100% of onsets fall within 1/16 beat
  of the tracked 16th grid (median 0.013 beats) at tightness 10–300; the performance is steady
  (±0.6 BPM), so it didn't test rubato hard. Key estimate D♭ major.
- **The video as an answer key** (`pianoscribe truth <song> --style pianox`). This style differs
  from Synthesia: bar colour encodes time-to-hit (red → purple → blue), not hand; blue particle
  effects swirl above the hit line; the pianist's hands cover the keys. So the reader:
  - finds the keyboard by rows whose *90th-percentile* brightness is white (row means dip under
    black keys and hands); the hit line is just above it;
  - fits a full 88-key layout to the black keys still visible in the per-pixel median (white
    37.34 px, residual 2.8 px), then snaps each key onto the bar columns seen falling on it,
    **one column per key** (letting a white key and its black neighbour snap to the same column
    read 79% of the notes twice);
  - reads the tape at the *top* of the screen, where bars are red and at full brightness while
    the effects are blue and dim (96% vs 4% of coloured pixels).
- **Result on a real performance: 86.2% note F1** (ensemble; P 88.5%, R 84.0%), octave right,
  audio/video lag +88 ms. Two caveats make it a slight *under*-estimate: ~10% of the video's
  notes still have a semitone twin (two key pairs whose bars glow into each other; dropping the
  dimmer twin didn't help, both read at full brightness), and note *ends* aren't comparable here
  (F1 with offsets 5–28%): the video draws how long each key was held (median 108 ms) while
  the pedal-heavy audio sustains ~3× longer.

## Third song: "Gymnopédie No. 1" (Satie, a Synthesia tutorial; the README's showcase)

Added through the viewer's own add-song API, in one step. Public domain, so it illustrates the
README.

- First non-4/4 piece: **3/4 detected** automatically; key **D major** (Satie's own); ♩ = 68.
  The score reads like the original: the alternating G/D bass, the chords, the melody.
- **Answer key: 83.2% note F1** (ensemble; P 85.4%, R 81.2%, lag +78 ms). This tutorial is
  another Synthesia variant: orange right hand (now mapped: blue = left, green/warm = right),
  a dark-grey background, note names printed on the keys (the white-key row moved up to 78% of
  the keyboard's height) and border lines at the frame edges (made two 0–1 px "keys"; now
  dropped).
- **Staff is not playing hand:** our hands score 72% here, below a fixed split (82%), because
  Satie notates the chords in the upper staff although the left hand leaps up to play them,
  and the video colours by playing hand. Our staff choice agrees with the printed original.
- Its bar lines don't read reliably (every 4.41 s, which fits no bar length of the piece; the grey
  background has its own faint lines), so this song's beat and rhythm rows in `eval` are
  meaningless; the notes and the 3/4 bars are right.

### Gotchas found along the way (keep in mind for other videos)

- White-key notes are drawn in **pastel** shades (light blue, saturation ~0.28) and black-key
  notes fully saturated (~1.0). White-key bars span the full key width, so they also cover the
  neighbouring black keys' columns. Black keys therefore only count saturated pixels, and white
  keys are sampled in their visible upper part.
- ffmpeg `crop` on yuv420p silently rounds odd sizes/offsets. Crop **after** `format=rgb24`,
  otherwise fixed-size raw reads drift out of frame alignment (or, with `exact=1`, chroma
  misaligns by a row).
- ffmpeg input seeking (`-ss`) is not frame-exact on every file. Anything needing exact frame
  indices streams from frame 0.
- A faint bar line can be detected twice, a few ms apart. Without merging, each duplicate
  became an extra bar, which silently capped beat/downbeat F at 97.6% and drifted the rhythm
  metric's bar numbering by whole bars.
- music21 writes clef *changes* in a two-staff part as bare `<clef>` elements. MusicXML reads
  that as staff 1, but `musicxml2ly` put a right-hand change on the left-hand staff. The
  exporter now numbers every clef by the staff of the next note after it.
- Assembling bars by hand skips music21's `makeNotation`, which is also what decides *printed*
  accidentals: every G♭ carried an explicit flat (2,270 of 2,655 notes) until
  `part.makeAccidentals()` ran again.
- music21 numbers voices 1, 2, ... in *both* staves of the joined part, and musicxml2ly groups
  notes by voice number across the part, merging the hands (barlines vanished, staves drifted).
  The exporter renumbers: staff 1 voices 1-4, staff 2 voices 5-8, as notation programs do.
- The Verovio wheel's compiled-in resource path is its build machine's temp folder. The package
  fixes the default at import, but not for other threads: in the server (requests run in worker
  threads) fonts failed to load and some scores wouldn't render. Each toolkit now sets it.
- Neither music21 nor Verovio is thread-safe: builds and previews take one lock.
- Spelling every note rebuilt a music21 scale each time (~8 ms × 4,800 notes): cached per key,
  a full build went from 11–17 s to 6–8 s and a preview from 14 s to ~3 s.
- **Our own MusicXML writer** (`notation/musicxml.py`) replaced music21 for building bars and
  exporting (~3.5 s of the remaining ~4.3 s preview): notatable values (largest first), ties,
  beams within beats, printed accidentals per staff and bar, per-staff voices, hidden rests as
  `<forward>`, clef changes before tied notes, key estimation (the same Krumhansl-Kessler profiles
  in numpy: same keys on all three songs). Notes carry ids `n<pitch>t<tick>v<voice>` that Verovio
  keeps, so the viewer gets pitches without 2,500 lookups. **Preview: 4.3 s → 0.56 s** (exile),
  0.38 s, 0.13 s. Checked by parsing the output back: every note (pitch, start, length, ties
  merged) matches the music21 version on 5 of 6 staves; the rest differ by 2–4 notes where a tie
  crossed from a one-voice into a two-voice bar (now both bars switch together), with every tie
  start matched by its stop.
- Intro/outro cards have other layouts. Frames where the red hit line is missing are skipped,
  and only the span the video covers is scored.

## Roadmap

1. ✅ **Spike:** repo reset, fetch, two backends, video ground truth, eval harness.
2. **Stage B core:** ✅ beats and bars → ✅ quantization → ✅ hand split → ✅ first score → ✅ voices
   by role → pickup bars, meter beyond 3/4-4/4.
3. ✅ **Stage C:** MusicXML → PDF via LilyPond (`musicxml2ly`).
4. ✅ **Viewer:** FastAPI + Verovio, synced to the original audio through the beat map. Next:
   hide a hand, practice mode with a MIDI keyboard.
5. ✅ **Add songs** from the viewer (search / link / upload) and ✅ **correct scores** in it.
   ✅ Video answer key for filmed-pianist (PianoX-style) videos. ✅ Faster previews (own
   MusicXML writer, 4.3 s → 0.56 s). ✅ Key, transpose, tempo, beat, meter and barline edits.
   Next: library sidebar, hands-separate synth playback, MIDI keyboard wait mode, fingering.
6. Stretch: Web MIDI practice mode (wait-for-correct-notes), video hand hints, Demucs for
   non-solo recordings.

## README images

`scripts/readme_images.py` drives the running viewer in the installed Chrome (Playwright,
`uv sync --extra docs`) and writes `docs/images/` (screenshots + the line-mode GIF).

## Setup (or just: `uv run pianoscribe add "<url>"`)

```bash
brew install uv ffmpeg lilypond
uv sync --extra transkun --extra bytedance
uv run pianoscribe download-models  # ByteDance + Beat This! checkpoints -> models/
uv run pianoscribe fetch "https://www.youtube.com/watch?v=UtGNBYegDBc" --song exile
uv run pianoscribe transcribe exile --backend all
uv run pianoscribe merge exile      # transkun onsets + ByteDance note ends -> notes/ensemble.mid
uv run pianoscribe beats exile      # beats + bars -> beats.json (checkpoint: models/beat_this/final0.ckpt)
uv run pianoscribe score exile --png  # -> library/exile/exile.pdf (+ MusicXML, page PNGs in score/)
uv run pianoscribe serve            # practice viewer on http://localhost:8765
uv run pianoscribe truth exile      # only for Synthesia-style videos: library/exile/source/video.mp4
uv run pianoscribe eval exile --plot
```
