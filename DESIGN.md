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
  notation/              # hands, score building (music21 -> MusicXML)
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
- Known limits: held notes under a moving line are cut (voices), note lengths from audio are only
  84% exact, 2/4 vs 4/4 and 6/8 aren't distinguished, no pickup (anacrusis) bars yet.

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
- Intro/outro cards have other layouts. Frames where the red hit line is missing are skipped,
  and only the span the video covers is scored.

## Roadmap

1. ✅ **Spike:** repo reset, fetch, two backends, video ground truth, eval harness.
2. **Stage B core:** ✅ beats and bars → ✅ quantization → ✅ hand split → ✅ first score (one voice
   per hand) → voices (held notes under moving lines), pickup bars.
3. ✅ **Stage C:** MusicXML → PDF via LilyPond (`musicxml2ly`).
4. **Viewer:** FastAPI + Verovio. The score is synced to the *original audio* through the beat
   map, so the cursor follows rubato. Loop sections, slow down, hide a hand; open it on an iPad.
5. **Search + library UI** (`yt-dlp "ytsearch10:<song> piano"`, pick from candidates).
6. Stretch: Web MIDI practice mode (wait-for-correct-notes), video hand hints, Demucs for
   non-solo recordings.

## Setup

```bash
brew install uv ffmpeg lilypond
uv sync --extra transkun --extra bytedance
# ByteDance checkpoint -> models/bytedance/ (see Stage A)
uv run pianoscribe fetch "https://www.youtube.com/watch?v=UtGNBYegDBc" --song exile
uv run pianoscribe transcribe exile --backend all
uv run pianoscribe merge exile      # transkun onsets + ByteDance note ends -> notes/ensemble.mid
uv run pianoscribe beats exile      # beats + bars -> beats.json (checkpoint: models/beat_this/final0.ckpt)
uv run pianoscribe score exile --png  # -> library/exile/exile.pdf (+ MusicXML, page PNGs in score/)
uv run pianoscribe truth exile      # only for Synthesia-style videos: library/exile/source/video.mp4
uv run pianoscribe eval exile --plot
```
