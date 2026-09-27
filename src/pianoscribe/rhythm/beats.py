"""Beat and downbeat tracking on the audio.

Beats come from the audio rather than a fixed BPM: every later stage measures time in
*beats*, so tempo drift and rubato are absorbed here instead of breaking the grid.

Beat This! (Foscarin et al. 2024) gives excellent per-frame beat/downbeat activations, but its
own peak picking has no notion of tempo continuity. On 'exile' it locked onto the off-beats for
30 s, dropped beats in sparse passages and burst into double time. So we keep its activations
and decode them ourselves: a dynamic-programming beat tracker (Ellis 2007) that trades
"land on strong activations" against "keep the tempo steady", then pick which beat is "one".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..transcribe.base import pick_device

DEFAULT_CHECKPOINT = Path("models/beat_this/final0.ckpt")
FPS = 50.0  # Beat This! activation rate: 22050 Hz audio, hop 441


@dataclass
class BeatGrid:
    beats: np.ndarray  # seconds, increasing; includes the downbeats
    downbeats: np.ndarray  # seconds; subset of `beats`

    @property
    def tempo(self) -> float:
        """Median tempo in beats per minute."""
        return float(60.0 / np.median(np.diff(self.beats)))

    @property
    def beats_per_bar(self) -> int:
        """Most common number of beats between consecutive downbeats (the meter's numerator)."""
        idx = np.searchsorted(self.beats, self.downbeats)
        counts = np.diff(idx)
        values, freq = np.unique(counts[counts > 0], return_counts=True)
        return int(values[np.argmax(freq)]) if len(values) else 4

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({
            "tempo": round(self.tempo, 2),
            "beats_per_bar": self.beats_per_bar,
            "beats": [round(float(t), 4) for t in self.beats],
            "downbeats": [round(float(t), 4) for t in self.downbeats],
        }))

    @classmethod
    def load(cls, path: Path) -> BeatGrid:
        d = json.loads(path.read_text())
        return cls(np.array(d["beats"]), np.array(d["downbeats"]))


@dataclass
class Activations:
    """Per-frame logits from Beat This! (cached so decoders can be tried without the model)."""

    beat_logits: np.ndarray
    downbeat_logits: np.ndarray
    fps: float = FPS

    @property
    def beat(self) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.beat_logits))

    @property
    def downbeat(self) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.downbeat_logits))

    def at(self, times: np.ndarray, which: str = "downbeat") -> np.ndarray:
        """Activation sampled at `times` (max over +-1 frame, beats may sit between frames)."""
        a = getattr(self, which)
        idx = np.clip(np.round(times * self.fps).astype(int), 1, len(a) - 2)
        return np.maximum.reduce([a[idx - 1], a[idx], a[idx + 1]])

    def save(self, path: Path) -> None:
        np.savez_compressed(path, beat=self.beat_logits, downbeat=self.downbeat_logits, fps=self.fps)

    @classmethod
    def load(cls, path: Path) -> Activations:
        d = np.load(path)
        return cls(d["beat"], d["downbeat"], float(d["fps"]))


def beat_activations(audio: np.ndarray, sr: int, device: str = "auto",
                     checkpoint: Path = DEFAULT_CHECKPOINT) -> Activations:
    from beat_this.inference import Audio2Frames

    if not checkpoint.exists():
        raise FileNotFoundError(f"Missing checkpoint {checkpoint} (see DESIGN.md, setup).")
    model = Audio2Frames(checkpoint_path=str(checkpoint), device=str(pick_device(device)))
    beat, downbeat = model(audio, sr)
    return Activations(beat.cpu().numpy(), downbeat.cpu().numpy())


def peak_picked(act: Activations) -> BeatGrid:
    """Beat This!'s own post-processing (no tempo model): the baseline to beat."""
    import torch
    from beat_this.model.postprocessor import Postprocessor

    beats, downbeats = Postprocessor(type="minimal")(torch.from_numpy(act.beat_logits),
                                                     torch.from_numpy(act.downbeat_logits))
    return BeatGrid(np.asarray(beats, float), np.asarray(downbeats, float))


def decode_beats(act: Activations, period: float, tightness: float = 300.0) -> np.ndarray:
    """Beat times (s) maximizing  sum(activation at beats) - tightness * sum(log(interval/period)^2).

    The log-ratio penalty is symmetric in tempo (a beat 10% early costs what 10% late does) and
    grows fast. At tightness 300 a phase flip or double-time burst (a 1.5x or 0.5x interval)
    costs ~50-145, against ~1 gained per real beat, while a 5% tempo change costs ~0.7.
    On 'exile' (beat F): peak picking 84%; tightness 30: 92%, 100: 91%, 300: 100%. That song is
    a constant-tempo MIDI rendering, so stiffer always wins there: re-check on a rubato recording.
    """
    a = act.beat
    tau = period * act.fps
    lags = np.arange(max(1, int(tau / 2)), int(2 * tau) + 1)
    penalty = -tightness * np.log(lags / tau) ** 2
    score = a.astype(float).copy()
    back = np.full(len(a), -1)
    for t in range(lags[0], len(a)):
        prev = t - lags
        ok = prev >= 0
        cand = score[prev[ok]] + penalty[ok]
        k = int(np.argmax(cand))
        score[t] += cand[k]
        back[t] = prev[ok][k]

    # Backtrack from the best-scoring frame in the last period.
    tail = int(tau)
    t = len(a) - tail + int(np.argmax(score[-tail:]))
    path = []
    while t >= 0:
        path.append(t)
        t = back[t]
    beats = np.array(path[::-1]) / act.fps

    # The chain fills silence with steady beats; trim the ones outside the music.
    strong = np.flatnonzero(a > 0.5) / act.fps
    if len(strong):
        beats = beats[(beats >= strong[0] - period / 2) & (beats <= strong[-1] + period / 2)]
    return beats


def phase_scores(strength: np.ndarray, beats_per_bar: int) -> np.ndarray:
    """Mean downbeat activation of each candidate phase: scores[p] rates beats[p::beats_per_bar].

    Mean, not sum: when the beat count isn't a multiple of `beats_per_bar`, the first phases
    get one extra beat. No outlier clipping: activations are probabilities in [0, 1], so one
    beat moves its phase's mean by at most 1/len (it would matter on raw logits).
    """
    if len(strength) < beats_per_bar:
        return np.zeros(beats_per_bar)
    return np.array([strength[p::beats_per_bar].mean() for p in range(beats_per_bar)])


def downbeat_phase(strength: np.ndarray, beats_per_bar: int) -> int:
    """Index of the first downbeat among the beats: 0 <= phase < beats_per_bar.

    strength[i] is the downbeat activation at beat i; downbeats will be beats[phase::beats_per_bar].
    """
    return int(np.argmax(phase_scores(strength, beats_per_bar)))


@dataclass
class BarChoice:
    beats_per_bar: int
    phase: int
    scores: np.ndarray  # phase_scores for the chosen meter

    @property
    def margin(self) -> float:
        """Best minus runner-up phase score. Small = ambiguous, often a half-bar (beat 3 in 4/4)."""
        top = np.sort(self.scores)[::-1]
        return float(top[0] - top[1]) if len(top) > 1 else float(top[0])


def choose_bars(strength: np.ndarray, meters: tuple[int, ...] = (4, 3)) -> BarChoice:
    """Pick meter and phase together: the meter whose best phase lines up the strongest downbeats.

    A wrong meter drifts against the true bars, mixing downbeats with other beats and diluting
    its best phase's mean. Ties go to the first meter listed (4/4 is the common case). 2/4 vs 4/4
    is left out on purpose: both line up equally well, and it is a notation choice anyway.
    """
    choices = [BarChoice(n, downbeat_phase(strength, n), phase_scores(strength, n)) for n in meters]
    return max(choices, key=lambda c: c.scores.max())


def track_beats(act: Activations, beats_per_bar: int | None = None,
                tightness: float = 300.0) -> tuple[BeatGrid, BarChoice]:
    """Beats (tempo-continuity DP) and downbeats. `beats_per_bar=None` detects 3 vs 4."""
    period = float(np.median(np.diff(peak_picked(act).beats)))  # robust to its local errors
    beats = decode_beats(act, period, tightness)
    strength = act.at(beats, "downbeat")
    meters = (beats_per_bar,) if beats_per_bar else (4, 3)
    bars = choose_bars(strength, meters)
    return BeatGrid(beats, beats[bars.phase :: bars.beats_per_bar]), bars
