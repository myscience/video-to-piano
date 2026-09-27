from __future__ import annotations

import argparse
import time
from pathlib import Path

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


def cmd_merge(args: argparse.Namespace) -> None:
    from .transcribe import Transcription
    from .transcribe.ensemble import merge

    song = Song.get(args.song)
    merged = merge(Transcription.load(song.notes(args.onsets)), Transcription.load(song.notes(args.offsets)))
    merged.save(song.notes(args.name))
    print(f"✓ {len(merged.notes)} notes (starts: {args.onsets}, ends: {args.offsets}) -> {song.notes(args.name)}")


def cmd_truth(args: argparse.Namespace) -> None:
    from .eval.synthesia import read_notes

    song = Song.get(args.song)
    video = Path(args.video) if args.video else song.root / "source.mp4"
    t0 = time.perf_counter()
    truth = read_notes(video)
    truth.save(song.notes("video"))
    hands = {h: sum(n.hand == h for n in truth.notes) for h in ("L", "R", None)}
    print(f"✓ {len(truth.notes)} notes (L={hands['L']}, R={hands['R']}, unknown={hands[None]}) "
          f"in {time.perf_counter() - t0:.0f}s -> {song.notes('video')}")


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
