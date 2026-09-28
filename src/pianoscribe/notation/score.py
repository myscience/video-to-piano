"""Quantized notes with hands -> a two-staff piano score -> MusicXML.

Each hand is written as one voice of chords, or, where it is worth it, two voices by musical role
(melody over accompaniment, bass under the rest). Gaps become rests only when they are real
silences (`written_end`). The bars are written straight to MusicXML (`musicxml.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import groupby

from ..rhythm.quantize import TICKS_PER_BEAT, QuantizedNote
from ..transcribe.base import Hand, NoteEvent, Transcription
from .hands import MAX_SPAN
from .musicxml import Bar, Key, Staff, Voice, diatonic, estimate_key, pieces_in_bars, write

# Gaps up to an eighth are always absorbed: on 'exile' the half-the-chord rule alone left 29
# sixteenth rests (a 16th note, then a 16th gap); with this floor none remain.
ABSORB_GAP = TICKS_PER_BEAT // 2


def in_key(pitches: list[int], weights: list[float], k: Key) -> float:
    """Weighted share of the notes that belong to the key's scale."""
    scale = k.scale
    return sum(w for p, w in zip(pitches, weights) if p % 12 in scale) / max(sum(weights), 1e-9)


def song_span(t: Transcription, min_silence: float = 1.0, min_fit: float = 0.85) -> tuple[float, float]:
    """(start, end) in seconds of the song itself, without intro/outro jingles.

    Splits the notes at silences (nothing sounding) of at least `min_silence`, takes the longest
    part as the song, then extends it outwards over neighbouring parts that sound like the same
    key (at least `min_fit` of their notes in its scale). A tutorial's jingle, in another key, stops it.
    """
    parts: list[list[NoteEvent]] = []
    end = -1.0
    for n in sorted(t.notes, key=lambda n: n.onset):
        if not parts or n.onset - end >= min_silence:
            parts.append([])
        parts[-1].append(n)
        end = max(end, n.offset)
    lengths = [max(n.offset for n in p) - p[0].onset for p in parts]
    main = int(max(range(len(parts)), key=lambda i: lengths[i]))

    def pw(part: list[NoteEvent]) -> tuple[list[int], list[float]]:
        return [n.pitch for n in part], [n.duration for n in part]

    k = estimate_key(*pw(parts[main]))
    lo = hi = main
    while lo > 0 and in_key(*pw(parts[lo - 1]), k) >= min_fit:
        lo -= 1
    while hi < len(parts) - 1 and in_key(*pw(parts[hi + 1]), k) >= min_fit:
        hi += 1
    return parts[lo][0].onset, max(n.offset for n in parts[hi])


def written_end(start: int, end: int, next_start: int | None, pedal_down: bool,
                max_bridge: int | None = None) -> int:
    """Where a chord's written notes end, in ticks from the first downbeat.

    start: where the chord starts. end: where its sound ends (snapped key release, > start).
    next_start: where this hand's next chord starts (None for its last chord); if end is past it,
        the notes get cut there (one voice per hand).
    pedal_down: whether the sustain pedal is held during the gap between end and next_start.
    max_bridge: if given, a gap longer than this always stays a rest. Used inside voices, where
        the next note can be bars away: bridging that would hold a note through other music.

    Returning next_start writes the chord legato into the next one; returning something earlier
    leaves a rest until next_start. Must satisfy start < result, and result <= next_start.

    Leans toward readability, as arrangers do: a gap is absorbed (written legato) when the pedal
    sustains the sound through it, when it is at most an eighth (never worth a rest), or when it
    is shorter than half the chord (a release, not a rest). Only clear silences stay rests.
    """
    if next_start is None:
        return end
    if end >= next_start:  # held under the next chord: one voice per hand, so cut there
        return next_start
    gap = next_start - end
    if max_bridge is not None and gap > max_bridge:
        return end
    if pedal_down or gap <= ABSORB_GAP or gap < (end - start) / 2:
        return next_start
    return end


def hand_events(notes: list[QuantizedNote], pedal: list[tuple[int, int]] = (),
                max_bridge: int | None = None) -> list[tuple[int, int, list[int]]]:
    """(start, length, pitches) per chord of one hand; empty pitches = rest. Ticks."""
    events = []
    groups = [list(g) for _, g in groupby(sorted(notes, key=lambda n: (n.start, n.pitch)), key=lambda n: n.start)]
    for i, group in enumerate(groups):
        start = group[0].start
        end = max(n.end for n in group)
        nxt = groups[i + 1][0].start if i + 1 < len(groups) else None
        locked = [n.end for n in group if n.locked]
        if locked:  # length set by an edit: exactly that, only cut by the hand's next chord
            end = min(max(locked), nxt) if nxt is not None else max(locked)
        elif nxt is not None:
            gap_lo, gap_hi = min(end, nxt), nxt
            held = any(a < gap_hi and b > gap_lo for a, b in pedal)
            end = written_end(start, end, nxt, held, max_bridge)
        events.append((start, end - start, sorted({n.pitch for n in group})))
        if nxt is not None and end < nxt:
            events.append((end, nxt - end, []))
    return events


def split_roles(notes: list[QuantizedNote], hand: Hand) -> tuple[list[QuantizedNote], list[QuantizedNote]]:
    """(upper voice, lower voice) by musical role, from starts and pitches only.

    Right hand: the top note of each chord is the melody (upper); the rest accompany (lower).
    Left hand: the bottom note is the bass (lower); the rest fill in above it (upper).
    (Taking only the lowest note of each *beat* as the bass, to split arpeggios, scattered the
    figures across both voices: 152 left-hand rests on 'exile' instead of 14.)
    """
    upper: list[QuantizedNote] = []
    lower: list[QuantizedNote] = []
    for _, g in groupby(sorted(notes, key=lambda n: (n.start, n.pitch)), key=lambda n: n.start):
        g = list(g)
        if hand == "R":
            upper.append(g[-1])
            lower += g[:-1]
        else:
            lower.append(g[0])
            upper += g[1:]
    return upper, lower


def voiced_bars(upper: list, lower: list, bar: int, n_bars: int, seed: list[bool] | None = None,
                also: list = ()) -> list[bool]:
    """Bars that need two voices: where the lower voice doesn't move in step with the upper one.

    Otherwise (block chords, or one role silent for the bar) plain chords read better. A note
    crossing a barline puts every bar it touches in the same mode, so ties never cross a mode change.
    """
    def rhythm(events: list) -> list[set]:
        per_bar: list[set] = [set() for _ in range(n_bars)]
        for s, length, pitches in events:
            if pitches:
                per_bar[s // bar].add((s, length))
        return per_bar

    up, low = rhythm(upper), rhythm(lower)
    need = seed if seed is not None else [bool(low[i]) and bool(up[i]) and not low[i] <= up[i]
                                          for i in range(n_bars)]
    # `also`: the one-voice version's events, whose ties must not cross a mode change either.
    spans = [(s // bar, (s + length - 1) // bar) for s, length, p in [*upper, *lower, *also] if p]
    changed = True
    while changed:
        changed = False
        for a, b in spans:
            if b > a and any(need[a : b + 1]) and not all(need[a : b + 1]):
                need[a : b + 1] = [True] * (b - a + 1)
                changed = True
    return need


def cut_out_of_reach(events: list, hand: list[QuantizedNote], reach: int = MAX_SPAN) -> list:
    """End a held note as soon as the same hand plays something beyond an octave from it: one
    hand can't hold it and play there (the remainder becomes a rest)."""
    out = []
    locked = {(n.start, n.pitch) for n in hand if n.locked}
    for s, length, pitches in events:
        if pitches and not any((s, p) in locked for p in pitches):
            far = [n.start for n in hand if s < n.start < s + length
                   and max(pitches + [n.pitch]) - min(pitches + [n.pitch]) > reach]
            if far:
                cut = min(far)
                out.append((s, cut - s, pitches))
                out.append((cut, s + length - cut, []))
                continue
        out.append((s, length, pitches))
    return out


def sustain_gain(events: list, onsets: list[int], bar: int, n_bars: int) -> list[int]:
    """Per bar, ticks of sustain a voice's notes keep beyond the hand's next onset: what writing
    the hand as one voice of chords would cut off."""
    gain = [0] * n_bars
    for s, length, pitches in events:
        if pitches:
            nxt = next((o for o in onsets if o > s), s + length)
            gain[s // bar] += max(0, s + length - nxt)
    return gain


# ---- building the staves -------------------------------------------------------------------

def build_staff(notes: list[QuantizedNote], hand: Hand, bar: int, n_bars: int, pedal: list[tuple[int, int]],
                voices: bool = True, min_gain: float = 1.0, per_rest: float = 0.5) -> tuple[list[Bar], int]:
    """One hand's bars. Returns them with the number of bars that use two voices.

    A bar gets two voices only where they are worth it: the sustain they show (that one voice
    would cut) is at least `min_gain` beats and at least `per_rest` beats per visible rest they
    add. The secondary voice (right-hand accompaniment, left-hand notes above the bass) hides its
    rests; the melody and the bass keep theirs, which carry musical meaning.
    """
    base = 1 if hand == "R" else 5  # MusicXML voices: staff 1 -> 1, 2; staff 2 -> 5, 6
    one_voice = hand_events(notes, pedal)
    merged = pieces_in_bars(one_voice, bar, n_bars)
    need = [False] * n_bars
    if voices:
        upper_notes, lower_notes = split_roles(notes, hand)
        upper = cut_out_of_reach(hand_events(upper_notes, pedal, TICKS_PER_BEAT), notes)
        lower = cut_out_of_reach(hand_events(lower_notes, pedal, TICKS_PER_BEAT), notes)
        ups = pieces_in_bars(upper, bar, n_bars, hidden_rests=hand == "L")
        lows = pieces_in_bars(lower, bar, n_bars, hidden_rests=hand == "R")
        onsets = sorted({n.start for n in notes})
        gain = [a + b for a, b in zip(sustain_gain(upper, onsets, bar, n_bars), sustain_gain(lower, onsets, bar, n_bars))]
        primary = lows if hand == "L" else ups
        rests = [sum(1 for p in primary[i] if not p.pitches and not p.hidden) for i in range(n_bars)]
        worth = [gain[i] >= min_gain * TICKS_PER_BEAT and gain[i] >= per_rest * TICKS_PER_BEAT * rests[i]
                 for i in range(n_bars)]
        diverge = voiced_bars(upper, lower, bar, n_bars)
        need = voiced_bars(upper, lower, bar, n_bars, [d and w for d, w in zip(diverge, worth)], one_voice)
    bars = [Bar([Voice(base, ups[i], "up"), Voice(base + 1, lows[i], "down")]) if need[i]
            else Bar([Voice(base, merged[i])]) for i in range(n_bars)]
    return bars, sum(need)


STAFF_LINES = {"treble": (30, 38), "bass": (18, 26)}  # outer lines as staff positions: E4-F5, G2-A3


def ledger_lines(midi: int, which: str, sharps: int) -> int:
    """Ledger lines a note needs on a treble or bass staff: every 2 diatonic steps past an outer
    line adds one (C4 on treble: 1; D4 hangs below the staff: 0)."""
    bottom, top = STAFF_LINES[which]
    d = diatonic(midi, sharps)
    return max(bottom - d, d - top, 0) // 2


def choose_clefs(bars: list[Bar], home: str, sharps: int, switch: float = 8.0, away: float = 1.0) -> list[str]:
    """Clef for every bar of a staff, chosen globally (dynamic programming over the bars).

    Minimizes the ledger lines of all notes, plus `switch` per clef change (a change must save
    more than that many ledger lines to be worth it) and `away` per bar in the other clef than
    the staff's `home` (a tie-breaker). The first bar's clef is free, so a low opening starts in
    bass clef instead of switching after one bar.
    """
    clefs = ("treble", "bass")
    cost, back = {c: 0.0 for c in clefs}, []
    for i, bar in enumerate(bars):
        pitches = [p for v in bar.voices for piece in v.pieces for p in piece.pitches]
        here = {c: sum(ledger_lines(p, c, sharps) for p in pitches) + (away if c != home else 0.0) for c in clefs}
        new, step = {}, {}
        for c in clefs:
            options = {c: cost[c]}
            if i > 0:
                other = clefs[1 - clefs.index(c)]
                options[other] = cost[other] + switch
            prev = min(options, key=options.get)
            new[c], step[c] = options[prev] + here[c], prev
        cost = new
        back.append(step)
    chosen = [min(cost, key=cost.get)]
    for step in reversed(back[1:]):
        chosen.append(step[chosen[-1]])
    return chosen[::-1]


def place_clefs(bars: list[Bar], clefs: list[str]) -> None:
    """Mark each clef change on its bar. Never mid-sustain: when a note is tied across that barline
    (pop syncopation does this in over half the bars), the change moves back to just before that
    note starts, as engravers do, so the whole held note sits in one clef."""
    for i in range(1, len(bars)):
        if clefs[i] == clefs[i - 1]:
            continue
        before = bars[i - 1].voices[0].pieces
        tied_in = bool(bars[i].voices[0].pieces) and bars[i].voices[0].pieces[0].tie_stop
        if tied_in and before and before[-1].tie_start and bars[i - 1].clef is None:
            k = len(before) - 1
            while k > 0 and before[k].tie_stop:
                k -= 1
            bars[i - 1].clef, bars[i - 1].clef_at = clefs[i], before[k].start
        else:
            bars[i].clef, bars[i].clef_at = clefs[i], 0


@dataclass
class Engraving:
    xml: str
    key: Key
    shift_beats: float  # score position (quarters from bar 1) = beat position + shift_beats
    voiced: dict[str, int]  # two-voice bars per hand


def build_score(notes: list[QuantizedNote], hands: list[Hand], beats_per_bar: int, bpm: float,
                title: str = "", composer: str = "", pedal: list[tuple[int, int]] = (),
                voices: bool = True, key: Key | None = None) -> Engraving:
    """pedal: sustain-pedal (down, up) intervals in ticks, on the same grid as the notes.
    voices: split each hand into melody/accompaniment (right) or bass/upper (left) where they
    move independently; otherwise one voice of chords per hand.
    key: the key to write in (default: estimated from the notes)."""
    key = key or estimate_key([n.pitch for n in notes], [n.duration / TICKS_PER_BEAT for n in notes])
    bar = beats_per_bar * TICKS_PER_BEAT
    shift = -(min(n.start for n in notes) // bar) * bar  # first note lands in bar 1 (drops empty bars)
    notes = [replace(n, start=n.start + shift, end=n.end + shift) for n in notes]
    pedal = [(a + shift, b + shift) for a, b in pedal]
    n_bars = -(-max(n.end for n in notes) // bar)

    staves, voiced, first = [], {}, {}
    for hand, number, home in (("R", 1, "treble"), ("L", 2, "bass")):
        bars, voiced[hand] = build_staff([n for n, h in zip(notes, hands) if h == hand], hand, bar, n_bars, pedal, voices)
        clefs = choose_clefs(bars, home, key.sharps)
        place_clefs(bars, clefs)
        first[number] = clefs[0]
        staves.append(Staff(number, bars))
    xml = write(staves, key, beats_per_bar, bpm, title, composer, first)
    return Engraving(xml, key, shift / TICKS_PER_BEAT, voiced)
