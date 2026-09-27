"""Which hand plays each note.

Audio carries no hand information, so this is inferred from the notes alone. A Synthesia video's
bar colors are the answer key (`pianoscribe eval`). On 'exile' the hands overlap by more than an
octave (left up to F#4, right down to D#3): a fixed split at middle C gets 91.1% of notes right,
and the rest can only be decided from context.

Two deciders share the same physical rules (a hand spans at most an octave and plays at most five
notes): `assign_hands` decides chord by chord from where the hands recently were (greedy, 94.9%),
`decode_hands` decides globally with dynamic programming (96.1%, the default).
"""

from __future__ import annotations

from collections import deque
from itertools import groupby

import numpy as np

from ..rhythm.quantize import QuantizedNote
from ..transcribe.base import Hand

MIDDLE_C = 60
# Widest chord one hand plays: an octave. On 'exile' 352 right-hand chords span exactly 12
# semitones and only 3 go wider.
MAX_SPAN = 12
MAX_NOTES = 5  # fingers


def split_fixed(notes: list[QuantizedNote], split: int = MIDDLE_C) -> list[Hand]:
    """Baseline: everything from `split` up is the right hand."""
    return ["R" if n.pitch >= split else "L" for n in notes]


def chords(notes: list[QuantizedNote]) -> list[list[int]]:
    """Indices of notes grouped by quantized start (in time order), each group sorted by pitch."""
    order = sorted(range(len(notes)), key=lambda i: (notes[i].start, notes[i].pitch))
    return [list(g) for _, g in groupby(order, key=lambda i: notes[i].start)]


def assign_hands(notes: list[QuantizedNote], memory: int = 8) -> list[Hand]:
    """Chord by chord, tracking where each hand currently is: the mean pitch of its last
    `memory` notes. Each chord is split into a lower (left) and an upper (right) part."""
    hands: list[Hand] = ["R"] * len(notes)
    recent: dict[Hand, deque[int]] = {"L": deque([48], maxlen=memory), "R": deque([72], maxlen=memory)}
    for group in chords(notes):
        pitches = [notes[i].pitch for i in group]
        k = split_chord(pitches, sum(recent["L"]) / len(recent["L"]), sum(recent["R"]) / len(recent["R"]))
        for rank, i in enumerate(group):
            hands[i] = "L" if rank < k else "R"
            recent[hands[i]].append(notes[i].pitch)
    return hands


def split_chord(pitches: list[int], left_center: float, right_center: float,
                max_span: int = MAX_SPAN, max_notes: int = MAX_NOTES) -> int:
    """How many of the chord's notes go to the left hand: the lowest k of them, 0 <= k <= len.

    pitches: the chord's MIDI pitches, ascending (a single note is a chord of one).
    left_center / right_center: mean pitch of each hand's recent notes (where the hands are now).
    """
    n = len(pitches)

    def span(lo: int, hi: int) -> int:  # pitch range of pitches[lo:hi]
        return pitches[hi - 1] - pitches[lo] if hi - lo > 1 else 0

    # 1. Nearest center: each note goes to the hand currently closer to it. With sorted pitches
    #    that is "below the midpoint". Counting that way stays sane if a run of mistakes drags
    #    the centers across each other (a literal nearest test would then send HIGH notes left).
    midpoint = (left_center + right_center) / 2
    k = sum(p < midpoint for p in pitches)

    # 2. Span: a hand wider than it can reach passes its innermost note to the other hand. Left
    #    first, then right: finds a split within reach for both whenever one exists.
    while k > 1 and span(0, k) > max_span:
        k -= 1
    while n - k > 1 and span(k, n) > max_span:
        k += 1

    # 3. Fingers: at most `max_notes` per hand, moving the innermost note over only if the
    #    receiving hand can still reach it.
    while k > max_notes and span(k - 1, n) <= max_span:
        k -= 1
    while n - k > max_notes and span(0, k + 1) <= max_span:
        k += 1
    return k


LOWEST, HIGHEST = 21, 108  # piano range, A0..C8
KEYS = HIGHEST - LOWEST + 1


def split_options(pitches: list[int], max_span: int = MAX_SPAN, max_notes: int = MAX_NOTES,
                  over_span: float = 10.0, over_notes: float = 10.0) -> list[tuple[int, float]]:
    """(k, cost) for each way to split a chord: lowest k notes left, the rest right.

    Splits where a hand stretches past `max_span` are dropped, unless no split fits (a chord
    wider than two hands, e.g. pedalled): then all are kept, charged per semitone of overstretch.
    Hands playing more than `max_notes` are charged per extra note.
    """
    n = len(pitches)

    def span(lo: int, hi: int) -> int:
        return pitches[hi - 1] - pitches[lo] if hi - lo > 1 else 0

    opts = [(k, max(0, span(0, k) - max_span) + max(0, span(k, n) - max_span),
             max(0, k - max_notes) + max(0, n - k - max_notes)) for k in range(n + 1)]
    fitting = [(k, over_notes * extra) for k, over, extra in opts if over == 0]
    return fitting or [(k, over_span * over + over_notes * extra) for k, over, extra in opts]


def decode_hands(notes: list[QuantizedNote], move: float = 1.0, free: int = 5, cross: float = 30.0,
                 start: tuple[int, int] = (48, 72), **limits) -> list[Hand]:
    """Globally best hand assignment: dynamic programming over where the two hands are.

    State: the pitch each hand is at (88 x 88). Each chord is split as in `split_options`; a
    hand that plays moves to the mean pitch of its notes, a hand that doesn't stays put. Cost:
    `move` per semitone a hand travels beyond `free` (a hand in position reaches a fifth without
    shifting), plus `cross` whenever the left hand sits above the right.
    Unlike the greedy `assign_hands`, a choice is judged by what it costs *later*: sending the
    right hand's dip to the left hand is expensive when the left must keep returning to its bass.

    On 'exile' (ensemble notes): greedy 94.9%; free 0: 95.1%, 5: 96.1%, 12: 94.7%. Anchoring a
    hand at its inner edge instead of its mean pitch was worse (94.5-94.8%); `cross` barely matters.
    """
    pos = np.arange(KEYS)
    travel = move * np.maximum(np.abs(pos[:, None] - pos[None, :]) - free, 0).astype(float)  # a -> b
    crossing = cross * (pos[:, None] > pos[None, :])  # [left, right]: left above right
    cost = travel[start[0] - LOWEST][:, None] + travel[start[1] - LOWEST][None, :] + crossing
    groups = chords(notes)
    back_split: list[np.ndarray] = []  # per chord: split k used to reach each state (-1: unreachable)
    back_prev: list[np.ndarray] = []  # per chord: flat index of the state it came from

    for group in groups:
        pitches = [notes[i].pitch for i in group]
        n = len(pitches)
        new = np.full((KEYS, KEYS), np.inf)
        split = np.full((KEYS, KEYS), -1, np.int8)
        prev = np.zeros((KEYS, KEYS), np.int16)
        for k, static in split_options(pitches, **limits):
            left = round(sum(pitches[:k]) / k) - LOWEST if k else None
            right = round(sum(pitches[k:]) / (n - k)) - LOWEST if k < n else None
            if left is not None and right is not None:  # both hands move: one target state
                total = cost + travel[:, left][:, None] + travel[:, right][None, :]
                i = int(np.argmin(total))
                c = total.flat[i] + static + crossing[left, right]
                if c < new[left, right]:
                    new[left, right], split[left, right], prev[left, right] = c, k, i
            elif right is not None:  # right hand only: every left position carries over
                total = cost + travel[:, right][None, :]
                r0 = np.argmin(total, axis=1)
                c = total[pos, r0] + static + crossing[:, right]
                better = c < new[:, right]
                new[better, right], split[better, right] = c[better], k
                prev[better, right] = pos[better] * KEYS + r0[better]
            else:  # left hand only
                total = cost + travel[:, left][:, None]
                l0 = np.argmin(total, axis=0)
                c = total[l0, pos] + static + crossing[left, :]
                better = c < new[left, :]
                new[left, better], split[left, better] = c[better], k
                prev[left, better] = l0[better] * KEYS + pos[better]
        cost = new
        back_split.append(split)
        back_prev.append(prev)

    hands: list[Hand] = ["R"] * len(notes)
    state = int(np.argmin(cost))
    for group, split, prev in zip(reversed(groups), reversed(back_split), reversed(back_prev)):
        left, right = divmod(state, KEYS)
        k = int(split[left, right])
        for rank, i in enumerate(group):
            hands[i] = "L" if rank < k else "R"
        state = int(prev[left, right])
    return hands
