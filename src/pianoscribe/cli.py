from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from .library import Song


def cmd_fetch(args: argparse.Namespace) -> None:
    from .sources.youtube import download_audio

    song = Song.get(args.song)
    wav = download_audio(args.url, song)
    print(f"✓ {song.read_meta().get('title')} -> {wav}")


def cmd_import(args: argparse.Namespace) -> None:
    from .sources.audio import extract_wav

    song = Song.get(args.song)
    extract_wav(Path(args.file), song.audio)
    song.update_meta(title=args.song, source_file=str(Path(args.file).resolve()))
    print(f"✓ {args.file} -> {song.audio}")


def cmd_transcribe(args: argparse.Namespace) -> None:
    from .sources.audio import load_audio
    from .transcribe import BACKENDS, get_transcriber

    song = Song.get(args.song)
    for name in BACKENDS if args.backend == "all" else [args.backend]:
        model = get_transcriber(name, args.device)
        audio = load_audio(song.audio, model.sample_rate)
        t0 = time.perf_counter()
        result = model.transcribe(audio)
        elapsed = time.perf_counter() - t0
        result.save(song.notes(name))
        print(f"✓ {name:10} on {model.device}: {len(result.notes)} notes, "
              f"{len(result.pedal)} pedal presses in {elapsed:.1f}s "
              f"({len(audio) / model.sample_rate / elapsed:.1f}x realtime) -> {song.notes(name)}")


def cmd_beats(args: argparse.Namespace) -> None:
    from .rhythm.beats import Activations, beat_activations, peak_picked, track_beats
    from .sources.audio import load_audio

    song = Song.get(args.song)
    t0 = time.perf_counter()
    if song.activations.exists() and not args.recompute:
        act = Activations.load(song.activations)
    else:
        act = beat_activations(load_audio(song.audio, 22_050), 22_050, args.device)
        act.save(song.activations)
    if args.method == "peaks":
        grid, bars = peak_picked(act), None
    else:
        grid, bars = track_beats(act, args.meter, args.tightness)
    grid.save(song.beats)
    print(f"✓ [{args.method}] {len(grid.beats)} beats, {len(grid.downbeats)} bars, ~{grid.tempo:.1f} BPM, "
          f"{grid.beats_per_bar} beats/bar in {time.perf_counter() - t0:.1f}s -> {song.beats}")
    if bars is not None:
        scores = " ".join(f"{x:.2f}" for x in bars.scores)
        print(f"  bar phase: {bars.phase} (phase scores {scores}; margin {bars.margin:.2f})")
        if bars.margin < 0.2:
            print("  ⚠ ambiguous bar phase (often a half-bar): check the barlines, or pass --meter")


def cmd_merge(args: argparse.Namespace) -> None:
    from .transcribe import Transcription
    from .transcribe.ensemble import merge

    song = Song.get(args.song)
    merged = merge(Transcription.load(song.notes(args.onsets)), Transcription.load(song.notes(args.offsets)))
    merged.save(song.notes(args.name))
    print(f"✓ {len(merged.notes)} notes (starts: {args.onsets}, ends: {args.offsets}) -> {song.notes(args.name)}")


def cmd_truth(args: argparse.Namespace) -> None:
    import json

    from .eval.synthesia import measure, read_bar_lines, read_notes

    song = Song.get(args.song)
    video = Path(args.video) if args.video else song.root / "source.mp4"
    t0 = time.perf_counter()
    geo = measure(video)
    truth = read_notes(video, geo)
    truth.save(song.notes("video"))
    hands = {h: sum(n.hand == h for n in truth.notes) for h in ("L", "R", None)}
    print(f"✓ {len(truth.notes)} notes (L={hands['L']}, R={hands['R']}, unknown={hands[None]}) "
          f"-> {song.notes('video')}")
    bars = read_bar_lines(video, geo)
    song.truth_bars.write_text(json.dumps({"downbeats": [round(float(t), 4) for t in bars]}))
    print(f"✓ {len(bars)} bar lines (every {np.median(np.diff(bars)):.3f}s) -> {song.truth_bars} "
          f"[{time.perf_counter() - t0:.0f}s]")


def cmd_eval(args: argparse.Namespace) -> None:
    from statistics import median

    from .eval.metrics import estimate_alignment, evaluate, shifted
    from .transcribe import Transcription

    song = Song.get(args.song)
    ref = Transcription.load(song.notes(args.reference))
    ests = {p.stem: Transcription.load(p) for p in sorted(song.notes(args.reference).parent.glob("*.mid"))
            if p.stem != args.reference}

    # The reference may be offset in time (video vs audio) and octave (keyboard naming): one
    # global correction, pooled over all backends so every backend is scored against the same truth.
    aligns = {name: estimate_alignment(ref, est) for name, est in ests.items()}
    for name, (shift, lag, support) in aligns.items():
        print(f"  alignment vs {name:10}: pitch {shift:+d}, lag {lag * 1000:+.0f} ms ({support} supporting pairs)")
    shifts = [a[0] for a in aligns.values()]
    shift = max(set(shifts), key=shifts.count)
    lag = median(a[1] for a in aligns.values())
    ref = shifted(ref, shift, lag)
    # Only score the span the reference covers (e.g. a video's outro card has no notes on screen).
    lo, hi = ref.notes[0].onset - 1.0, max(n.offset for n in ref.notes) + 1.0
    ests = {name: Transcription([n for n in est.notes if lo <= n.onset <= hi], est.pedal)
            for name, est in ests.items()}
    print(f"  scoring {lo:.1f}s .. {hi:.1f}s (reference span)")

    print(f"\n{'backend':10} {'notes':>6} {'P':>6} {'R':>6} {'F1':>6} {'F1+off':>7}   (onset ±50 ms; +off: offset within 20%)")
    print(f"{args.reference:10} {len(ref.notes):6d}   (reference)")
    for name, est in ests.items():
        m = evaluate(ref, est)
        print(f"{name:10} {len(est.notes):6d} {m['precision']:6.1%} {m['recall']:6.1%} {m['f1']:6.1%} "
              f"{m['f1_with_offsets']:7.1%}")

    if song.beats.exists() and song.truth_bars.exists():
        import json

        from .eval.metrics import evaluate_beats
        from .rhythm.beats import BeatGrid

        ref_bars = np.array(json.loads(song.truth_bars.read_text())["downbeats"]) + lag
        grid = BeatGrid.load(song.beats)
        m = evaluate_beats(ref_bars, grid)
        print(f"\nbeats   : F {m['beat_f']:6.1%}  (reference: {m['ref_beats_per_bar']} beats per video bar)")
        print(f"downbeats: F {m['downbeat_f']:6.1%}  ({len(grid.downbeats)} tracked vs {len(ref_bars)} bar lines)")

        # Rhythm: each side quantized on its own grid, then compared in ticks. Both grids are
        # numbered in beats from their first downbeat; line them up at the video's first bar.
        from dataclasses import replace

        from .eval.metrics import evaluate_rhythm, grid_from_bars
        from .rhythm.quantize import TICKS_PER_BEAT, quantize, to_beats

        offset = round(float(to_beats(ref_bars[:1], grid)[0])) * TICKS_PER_BEAT
        qref = [replace(q, start=q.start + offset, end=q.end + offset)
                for q in quantize(ref, grid_from_bars(ref_bars, m["ref_beats_per_bar"]))]
        print(f"\n{'rhythm':10} {'pairs':>6} {'start':>7} {'length':>7} {'short':>6} {'long':>6}"
              "   (16th grid; share of shared notes written exactly like the reference)")
        quantized = {name: quantize(est, grid) for name, est in ests.items()}
        for name, est in ests.items():
            r = evaluate_rhythm(qref, quantized[name], ref, est)
            print(f"{name:10} {r['pairs']:6d} {r['start_exact']:7.1%} {r['duration_exact']:7.1%} "
                  f"{r['duration_too_short']:6.1%} {r['duration_too_long']:6.1%}")

        if any(n.hand for n in ref.notes):
            from .eval.metrics import evaluate_hands
            from .notation.hands import assign_hands, decode_hands, split_fixed

            print(f"\n{'hands':10} {'split C4':>9} {'greedy':>7} {'dp':>7}   (share of shared notes given the correct hand)")
            for name, est in ests.items():
                q = quantized[name]
                accs = [evaluate_hands(ref, est, f(q)) for f in (split_fixed, assign_hands, decode_hands)]
                print(f"{name:10} {accs[0]:9.1%} {accs[1]:7.1%} {accs[2]:7.1%}")

    if args.plot:
        from .eval.plots import piano_roll

        out = song.root / "eval" / "roll.png"
        piano_roll(ref, ests, out, start=args.plot_start, length=args.plot_length)
        print(f"\n✓ piano roll -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pianoscribe")
    sub = parser.add_subparsers(required=True)

    p = sub.add_parser("fetch", help="Download audio from a URL (YouTube, ...)")
    p.add_argument("url")
    p.add_argument("--song", required=True, help="Library slug, e.g. 'exile'")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("import", help="Import a local audio/video file")
    p.add_argument("file")
    p.add_argument("--song", required=True)
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("transcribe", help="Audio -> notes/<backend>.mid")
    p.add_argument("song")
    p.add_argument("--backend", default="transkun", choices=["transkun", "bytedance", "all"])
    p.add_argument("--device", default="auto", help="auto | cpu | mps | cuda")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("beats", help="Beat + downbeat tracking on the audio -> beats.json")
    p.add_argument("song")
    p.add_argument("--method", default="dp", choices=["dp", "peaks"],
                   help="dp: tempo-continuity decoding (default); peaks: Beat This! peak picking")
    p.add_argument("--meter", type=int, default=None, help="Beats per bar (default: detect 3 vs 4)")
    p.add_argument("--tightness", type=float, default=300.0, help="dp: how strongly to keep tempo steady")
    p.add_argument("--recompute", action="store_true", help="Ignore cached activations")
    p.add_argument("--device", default="auto", help="auto | cpu | mps | cuda")
    p.set_defaults(func=cmd_beats)

    p = sub.add_parser("merge", help="Combine two transcriptions: starts from one, ends from another")
    p.add_argument("song")
    p.add_argument("--onsets", default="transkun")
    p.add_argument("--offsets", default="bytedance")
    p.add_argument("--name", default="ensemble", help="Output: notes/<name>.mid")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser("truth", help="Ground-truth notes from a Synthesia-style video -> notes/video.mid")
    p.add_argument("song")
    p.add_argument("--video", help="Defaults to library/<song>/source.mp4")
    p.set_defaults(func=cmd_truth)

    p = sub.add_parser("eval", help="Score every notes/*.mid against a reference transcription")
    p.add_argument("song")
    p.add_argument("--reference", default="video")
    p.add_argument("--plot", action="store_true", help="Also save a piano-roll comparison image")
    p.add_argument("--plot-start", type=float, default=60.0)
    p.add_argument("--plot-length", type=float, default=8.0)
    p.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    args.func(args)
