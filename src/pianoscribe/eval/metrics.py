"""Compare a transcription against a reference (mir_eval's standard AMT metrics)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import mir_eval
import numpy as np

from ..rhythm.beats import BeatGrid
from ..transcribe.base import Transcription


def estimate_alignment(
    ref: Transcription, est: Transcription, max_lag: float = 1.0, shifts=range(-36, 37, 12)
) -> tuple[int, float, int]:
    """Global (pitch shift, time lag) that best maps `ref` onto `est`.

    Collects onset differences between same-pitch note pairs; the true lag is the sharp peak of
    that histogram, and the true octave shift is the one whose peak collects the most pairs.
    Returns (shift, lag, n_supporting_pairs).
    """
    est_onsets = defaultdict(list)
    for n in est.notes:
        est_onsets[n.pitch].append(n.onset)
    est_arr = {p: np.array(v) for p, v in est_onsets.items()}

    best = (0, 0.0, -1)
    bins = np.arange(-max_lag, max_lag + 1e-9, 0.005)
    for shift in shifts:
        ref_onsets = defaultdict(list)
        for n in ref.notes:
            ref_onsets[n.pitch + shift].append(n.onset)
        diffs = [
            d
            for p, ons in ref_onsets.items()
            if p in est_arr
            for d in np.subtract.outer(est_arr[p], np.array(ons)).ravel()
            if abs(d) < max_lag
        ]
        if not diffs:
            continue
        hist, _ = np.histogram(diffs, bins)
        support = np.convolve(hist, np.ones(9, int), mode="same")  # +-20 ms window
        peak = int(np.argmax(support))
        if support[peak] > best[2]:
            best = (shift, float(bins[peak] + 0.0025), int(support[peak]))
    return best


def shifted(t: Transcription, pitch: int = 0, lag: float = 0.0) -> Transcription:
    """Move every note by `pitch` semitones and `lag` seconds, dropping notes pushed before 0."""
    notes = [replace(n, pitch=n.pitch + pitch, onset=n.onset + lag, offset=n.offset + lag)
             for n in t.notes if n.onset + lag >= 0]
    return Transcription(notes, [(max(a + lag, 0.0), b + lag) for a, b in t.pedal if b + lag > 0])


def evaluate(ref: Transcription, est: Transcription, onset_tolerance: float = 0.05) -> dict[str, float]:
    ri, rp = ref.intervals_and_pitches()
    ei, ep = est.intervals_and_pitches()
    p, r, f, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ri, rp, ei, ep, onset_tolerance=onset_tolerance, offset_ratio=None)
    p_off, r_off, f_off, _ = mir_eval.transcription.precision_recall_f1_overlap(
        ri, rp, ei, ep, onset_tolerance=onset_tolerance, offset_ratio=0.2)
    return {"precision": p, "recall": r, "f1": f, "f1_with_offsets": f_off, "precision_with_offsets": p_off,
            "recall_with_offsets": r_off}


def grid_from_bars(downbeats: np.ndarray, per_bar: int) -> BeatGrid:
    """Beat grid that splits every bar evenly into `per_bar` beats."""
    beats = np.concatenate([np.linspace(a, b, per_bar, endpoint=False)
                            for a, b in zip(downbeats[:-1], downbeats[1:])] + [downbeats[-1:]])
    return BeatGrid(beats, downbeats)


def evaluate_beats(ref_downbeats: np.ndarray, est: BeatGrid, window: float = 0.07) -> dict[str, float]:
    """Beat/downbeat F-measure (mir_eval, ±70 ms). Reference beats split each reference bar
    evenly into the whole number of tracked beats that best fits the median bar length."""
    per_bar = max(1, round(float(np.median(np.diff(ref_downbeats))) / float(np.median(np.diff(est.beats)))))
    ref_beats = grid_from_bars(ref_downbeats, per_bar).beats
    lo, hi = ref_downbeats[0] - window, ref_downbeats[-1] + window  # score only the covered span
    def clip(x: np.ndarray) -> np.ndarray:
        return x[(x >= lo) & (x <= hi)]
    return {
        "beat_f": mir_eval.beat.f_measure(ref_beats, clip(est.beats), window),
        "downbeat_f": mir_eval.beat.f_measure(ref_downbeats, clip(est.downbeats), window),
        "ref_beats_per_bar": per_bar,
    }


def evaluate_rhythm(ref: list, est: list, ref_t: Transcription, est_t: Transcription,
                    onset_tolerance: float = 0.05) -> dict[str, float]:
    """Quantized-position accuracy over the notes both transcriptions share.

    ref/est: QuantizedNote lists parallel to ref_t.notes / est_t.notes (both already on a common
    bar numbering). Notes are paired by mir_eval (same pitch, onsets within tolerance in seconds),
    then compared in ticks: is the note written at the same spot, with the same length?
    """
    ri, rp = ref_t.intervals_and_pitches()
    ei, ep = est_t.intervals_and_pitches()
    pairs = mir_eval.transcription.match_notes(ri, rp, ei, ep, onset_tolerance=onset_tolerance, offset_ratio=None)
    same_start = [ref[i].start == est[j].start for i, j in pairs]
    same_len = [ref[i].duration == est[j].duration for i, j in pairs]
    ratio = [est[j].duration / ref[i].duration for i, j in pairs]
    return {
        "pairs": len(pairs),
        "start_exact": float(np.mean(same_start)),
        "duration_exact": float(np.mean(same_len)),
        "duration_too_short": float(np.mean([r < 1 for r in ratio])),
        "duration_too_long": float(np.mean([r > 1 for r in ratio])),
    }


def evaluate_hands(ref_t: Transcription, est_t: Transcription, est_hands: list,
                   onset_tolerance: float = 0.05) -> float:
    """Share of shared notes (paired as in `evaluate`) assigned to the same hand as the reference."""
    ri, rp = ref_t.intervals_and_pitches()
    ei, ep = est_t.intervals_and_pitches()
    pairs = mir_eval.transcription.match_notes(ri, rp, ei, ep, onset_tolerance=onset_tolerance, offset_ratio=None)
    return float(np.mean([ref_t.notes[i].hand == est_hands[j] for i, j in pairs]))
