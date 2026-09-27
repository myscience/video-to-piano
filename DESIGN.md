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
  cli.py                 # pianoscribe fetch | import | transcribe | truth | eval
  library.py             # Song: per-song folder paths + meta.json
  sources/               # yt-dlp download, ffmpeg decode -> mono 44.1 kHz audio.wav
  transcribe/            # NoteEvent/Transcription + pluggable backends (Transcriber protocol)
  eval/                  # Synthesia-video ground truth, mir_eval metrics, piano-roll plots
  rhythm/ notation/ render/ server/     # planned (stages B, C, viewer)
web/                     # planned: viewer
library/<song>/          # gitignored: source_audio.m4a, audio.wav, meta.json,
                         #   notes/{transkun,bytedance,video}.mid, eval/roll.png, ...
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
  (`truth_bars.json`). They show up as a lift of the *median* brightness across the width, which
  note bars never cause.

| 'exile' (87 bars, 4/4, 74 BPM) | beat F | downbeat F |
|---|---|---|
| Beat This! peak picking | 82.3% | 67.9% (it guessed 2 beats/bar) |
| DP (tightness 300) + phase | **97.6%** | **97.6%** (every true bar line hit) |

Caveat: 'exile' is a constant-tempo MIDI rendering, so the stiffest tightness always wins there
(30 → 90%, 100 → 89%, 300 → 98%). Re-check on a rubato recording before trusting the default.

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
- Intro/outro cards have other layouts. Frames where the red hit line is missing are skipped,
  and only the span the video covers is scored.

## Roadmap

1. ✅ **Spike:** repo reset, fetch, two backends, video ground truth, eval harness.
2. **Stage B core:** ✅ beats and bars → onset quantization on the beat grid → hand split (scored
   against the video's hand colors) → `music21` score → MusicXML.
3. **Stage C:** MusicXML → PDF (LilyPond via `musicxml2ly`, or Verovio).
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
uv run pianoscribe truth exile      # only for Synthesia-style videos, needs library/exile/source.mp4
uv run pianoscribe eval exile --plot
```
