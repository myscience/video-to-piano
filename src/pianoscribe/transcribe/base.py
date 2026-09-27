"""Core data model for the perception stage: audio -> note events.

A transcription is a flat list of independent notes. Chords, voices and
hands are *notation* decisions made downstream; nothing here groups notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import pretty_midi
import torch

SUSTAIN_CC = 64
Hand = Literal["L", "R"]
TRACK_NAMES: dict[Hand | None, str] = {"L": "Left", "R": "Right", None: "Piano"}


@dataclass(frozen=True, slots=True)
class NoteEvent:
    pitch: int  # MIDI number: 21 (A0) .. 108 (C8)
    onset: float  # seconds from start of audio
    offset: float  # seconds from start of audio
    velocity: int  # 1..127
    hand: Hand | None = None  # unknown until the notation stage (or a video) says so

    @property
    def duration(self) -> float:
        return self.offset - self.onset


@dataclass(slots=True)
class Transcription:
    notes: list[NoteEvent]
    # Sustain pedal intervals as (down, up) in seconds.
    pedal: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.notes.sort(key=lambda n: (n.onset, n.pitch))

    def to_midi(self) -> pretty_midi.PrettyMIDI:
        """One track per hand when hands are known, else a single 'Piano' track."""
        pm = pretty_midi.PrettyMIDI(resolution=960)
        tracks: dict[Hand | None, pretty_midi.Instrument] = {}
        for n in self.notes:
            if n.hand not in tracks:
                tracks[n.hand] = pretty_midi.Instrument(program=0, name=TRACK_NAMES[n.hand])
            tracks[n.hand].notes.append(
                pretty_midi.Note(velocity=n.velocity, pitch=n.pitch, start=n.onset, end=n.offset)
            )
        first = next(iter(tracks.values()), None) or pretty_midi.Instrument(program=0, name="Piano")
        for down, up in self.pedal:
            first.control_changes.append(pretty_midi.ControlChange(SUSTAIN_CC, 127, down))
            first.control_changes.append(pretty_midi.ControlChange(SUSTAIN_CC, 0, up))
        pm.instruments = list(tracks.values()) or [first]
        return pm

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.to_midi().write(str(path))

    @classmethod
    def load(cls, path: Path) -> Transcription:
        pm = pretty_midi.PrettyMIDI(str(path))
        notes, pedal = [], []
        hand_of = {name: hand for hand, name in TRACK_NAMES.items()}
        for inst in pm.instruments:
            hand = hand_of.get(inst.name)
            notes += [NoteEvent(n.pitch, n.start, n.end, n.velocity, hand) for n in inst.notes]
            down = None
            for cc in sorted(inst.control_changes, key=lambda c: c.time):
                if cc.number != SUSTAIN_CC:
                    continue
                if cc.value >= 64 and down is None:
                    down = cc.time
                elif cc.value < 64 and down is not None:
                    pedal.append((down, cc.time))
                    down = None
        return cls(notes, pedal)

    def intervals_and_pitches(self) -> tuple[np.ndarray, np.ndarray]:
        """(N,2) onset/offset array and (N,) Hz array, the format mir_eval expects."""
        intervals = np.array([[n.onset, n.offset] for n in self.notes]).reshape(-1, 2)
        hz = np.array([pretty_midi.note_number_to_hz(n.pitch) for n in self.notes])
        return intervals, hz


class Transcriber(Protocol):
    name: str
    sample_rate: int  # rate the model wants its input at

    def transcribe(self, audio: np.ndarray) -> Transcription:
        """audio: mono float32 at `self.sample_rate`."""
        ...


def pick_device(preferred: str = "auto") -> torch.device:
    if preferred != "auto":
        return torch.device(preferred)
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")
