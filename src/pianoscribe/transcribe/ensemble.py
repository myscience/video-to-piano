"""Combine two transcriptions: note starts from one backend, note ends from another.

On 'exile', transkun has the best onsets (95.7% F1, 98.3% precision) while ByteDance has far
better note ends (79.5% vs 58.6% F1 with offsets). Note ends decide notated durations
(quarter vs eighth), so it's worth borrowing them.
"""

from __future__ import annotations

import math
from dataclasses import replace

import mir_eval

from .base import NoteEvent, Transcription


def merge(
    onsets: Transcription,
    offsets: Transcription,
    onset_tolerance: float = 0.05,
    min_duration: float = 0.03,
) -> Transcription:
    """The notes of `onsets`, with note ends borrowed from the matching notes of `offsets`.

    `onsets` is the backbone: it alone decides which notes exist. Notes only `offsets` found
    are dropped (on 'exile' they cost 1.6% precision for 0.2% recall). A borrowed end is capped
    at the next strike of the same key (MIDI can't hold two overlapping notes on one key: they
    get mis-paired on reload) and used only if the note still lasts `min_duration` (transkun
    never outputs notes under ~34 ms); otherwise the backbone keeps its own end.
    """
    oi, op = onsets.intervals_and_pitches()
    fi, fp = offsets.intervals_and_pitches()
    # match[i] = j  <=>  onsets.notes[i] and offsets.notes[j] are the same note: same pitch,
    # onsets within `onset_tolerance`. One-to-one; notes without a partner are absent.
    match: dict[int, int] = dict(mir_eval.transcription.match_notes(
        oi, op, fi, fp, onset_tolerance=onset_tolerance, offset_ratio=None))

    # Time of the next strike of the same key, for every backbone note (notes are onset-sorted).
    next_strike = [math.inf] * len(onsets.notes)
    later: dict[int, float] = {}
    for i in reversed(range(len(onsets.notes))):
        pitch = onsets.notes[i].pitch
        next_strike[i] = later.get(pitch, math.inf)
        later[pitch] = onsets.notes[i].onset

    notes: list[NoteEvent] = []
    for i, note in enumerate(onsets.notes):
        j = match.get(i)
        if j is not None:
            end = min(offsets.notes[j].offset, next_strike[i])
            if end - note.onset >= min_duration:
                note = replace(note, offset=end)
        notes.append(note)

    # Pedal detection is ByteDance's strength too (91.9% pedal F1 on MAESTRO).
    return Transcription(notes, pedal=offsets.pedal)
