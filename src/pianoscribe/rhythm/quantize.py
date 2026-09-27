"""Quantization: note times (seconds) -> exact positions on the beat grid (beats).

Principle: quantize *positions*, not durations. Every note start and end is snapped on its
own to the grid, and a duration is the difference of two snapped positions, so rounding errors
can never accumulate and drift the barlines (the old codebase's main failure).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..transcribe.base import Hand, Transcription
from .beats import BeatGrid


def to_beats(times: np.ndarray, grid: BeatGrid) -> np.ndarray:
    """Continuous beat positions of `times`: 0 = first downbeat, 1.5 = halfway through beat 2.

    Linear between tracked beats, so tempo changes are absorbed; before the first / after the
    last beat, the edge beat interval is extended (pickup notes get negative positions).
    """
    beats = grid.beats
    index = np.arange(len(beats), dtype=float) - np.searchsorted(beats, grid.downbeats[0])
    pos = np.interp(times, beats, index)
    first, last = beats[1] - beats[0], beats[-1] - beats[-2]
    pos = np.where(times < beats[0], index[0] - (beats[0] - times) / first, pos)
    return np.where(times > beats[-1], index[-1] + (times - beats[-1]) / last, pos)


# Positions are integer ticks: 12 per beat holds 16ths (3), 8th triplets (4) and 16th triplets (2).
TICKS_PER_BEAT = 12


@dataclass(frozen=True, slots=True)
class QuantizedNote:
    pitch: int
    start: int  # ticks from the first downbeat (negative = pickup)
    end: int  # ticks, > start
    velocity: int
    hand: Hand | None = None

    @property
    def duration(self) -> int:
        return self.end - self.start


def snap(pos: np.ndarray, step: int) -> np.ndarray:
    """Nearest multiple of `step` ticks, for positions given in beats."""
    return (np.round(pos * TICKS_PER_BEAT / step) * step).astype(int)


def quantize(t: Transcription, grid: BeatGrid, step: int = 3) -> list[QuantizedNote]:
    """Snap every note start and end, independently, to a grid of `step` ticks (3 = 16ths)."""
    starts = snap(to_beats(np.array([n.onset for n in t.notes]), grid), step)
    ends = snap(to_beats(np.array([n.offset for n in t.notes]), grid), step)
    ends = np.maximum(ends, starts + step)  # nothing shorter than one grid step
    return [QuantizedNote(n.pitch, int(s), int(e), n.velocity, n.hand) for n, s, e in zip(t.notes, starts, ends)]
