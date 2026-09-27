"""Quantized notes with hands -> a two-staff piano score (music21) -> MusicXML.

First version, deliberately simple: one voice per hand. Notes starting together form a chord,
each chord lasts until that hand's next chord unless a clear silence follows (`written_end`).
Held notes under a moving line are cut at the next chord (voices will fix that).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from itertools import groupby
from pathlib import Path

from music21 import chord, clef, key, layout, meter, metadata, note, pitch, stream, tempo

from ..rhythm.quantize import TICKS_PER_BEAT, QuantizedNote
from ..transcribe.base import Hand, NoteEvent, Transcription

# Gaps up to an eighth are always absorbed: on 'exile' the half-the-chord rule alone left 29
# sixteenth rests (a 16th note, then a 16th gap); with this floor none remain.
ABSORB_GAP = TICKS_PER_BEAT // 2


def estimate_key(pitches: list[int], weights: list[float]) -> key.Key:
    """Krumhansl-Schmuckler on weighted pitches; 6+ accidentals -> the flat-side enharmonic key."""
    s = stream.Stream()
    for p, w in zip(pitches, weights):
        s.append(note.Note(p, quarterLength=max(w, 0.01)))
    k = s.analyze("key")
    if abs(k.sharps) > 6 or k.sharps == 6:  # prefer Gb over F#, Db over C#, ...
        k = key.Key(k.tonic.getEnharmonic().name, k.mode)
    return k


def spell(midi: int, k: key.Key) -> pitch.Pitch:
    """The key's own name for scale notes (Bb, not A#, in Gb major); chromatic notes lean the
    way the key signature does (flats in flat keys, sharps in sharp keys)."""
    scale = {p.pitchClass: p.name for p in k.getScale(k.mode).getPitches()}
    p = pitch.Pitch(midi=midi)
    if midi % 12 in scale:
        p = pitch.Pitch(scale[midi % 12])
    elif p.accidental is not None and (p.accidental.alter > 0) != (k.sharps > 0):
        p = p.getEnharmonic()
    p.octave = 4
    p.octave += (midi - round(p.ps)) // 12  # keep the sounding pitch (Cb4 sounds as B3)
    return p


def in_key(pitches: list[int], weights: list[float], k: key.Key) -> float:
    """Weighted share of the notes that belong to the key's scale."""
    scale = {p.pitchClass for p in k.getScale(k.mode).getPitches()}
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


def written_end(start: int, end: int, next_start: int | None, pedal_down: bool) -> int:
    """Where a chord's written notes end, in ticks from the first downbeat.

    start: where the chord starts. end: where its sound ends (snapped key release, > start).
    next_start: where this hand's next chord starts (None for its last chord); if end is past it,
        the notes get cut there (one voice per hand).
    pedal_down: whether the sustain pedal is held during the gap between end and next_start.

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
    if pedal_down or gap <= ABSORB_GAP or gap < (end - start) / 2:
        return next_start
    return end


def hand_events(notes: list[QuantizedNote], pedal: list[tuple[int, int]] = ()) -> list[tuple[int, int, list[int]]]:
    """(start, length, pitches) per chord of one hand; empty pitches = rest. Ticks."""
    events = []
    groups = [list(g) for _, g in groupby(sorted(notes, key=lambda n: (n.start, n.pitch)), key=lambda n: n.start)]
    for i, group in enumerate(groups):
        start = group[0].start
        end = max(n.end for n in group)
        nxt = groups[i + 1][0].start if i + 1 < len(groups) else None
        if nxt is not None:
            gap_lo, gap_hi = min(end, nxt), nxt
            held = any(a < gap_hi and b > gap_lo for a, b in pedal)
            end = written_end(start, end, nxt, held)
        events.append((start, end - start, sorted({n.pitch for n in group})))
        if nxt is not None and end < nxt:
            events.append((end, nxt - end, []))
    return events


def build_score(notes: list[QuantizedNote], hands: list[Hand], beats_per_bar: int, bpm: float,
                title: str = "", composer: str = "", pedal: list[tuple[int, int]] = ()) -> stream.Score:
    """pedal: sustain-pedal (down, up) intervals in ticks, on the same grid as the notes."""
    k = estimate_key([n.pitch for n in notes], [n.duration / TICKS_PER_BEAT for n in notes])
    bar = beats_per_bar * TICKS_PER_BEAT
    shift = -(min(n.start for n in notes) // bar) * bar  # first note lands in bar 1 (drops empty bars)

    score = stream.Score()
    # Only the movement title: music21 would also copy `title` into it (shown twice as a subtitle).
    score.metadata = metadata.Metadata(movementName=title, composer=composer)
    staves = []
    for hand, staff_clef in (("R", clef.TrebleClef()), ("L", clef.BassClef())):
        part = stream.PartStaff()
        part.append([staff_clef, k, meter.TimeSignature(f"{beats_per_bar}/4")])
        if hand == "R":
            part.append(tempo.MetronomeMark(number=round(bpm)))
        mine = [n for n, h in zip(notes, hands) if h == hand]
        for start, length, pitches in hand_events(mine, pedal):
            ql = Fraction(length, TICKS_PER_BEAT)
            if not pitches:
                el = note.Rest(quarterLength=ql)
            elif len(pitches) == 1:
                el = note.Note(spell(pitches[0], k), quarterLength=ql)
            else:
                el = chord.Chord([spell(p, k) for p in pitches], quarterLength=ql)
            part.insert(Fraction(start + shift, TICKS_PER_BEAT), el)
        staves.append(part)
        score.insert(0, part)
    score.insert(0, layout.StaffGroup(staves, symbol="brace", barTogether=True))
    score = score.makeNotation()
    for part, home in zip(score.parts, ("treble", "bass")):
        add_clef_changes(part, home)
    return score


STAFF_LINES = {"treble": ("E4", "F5"), "bass": ("G2", "A3")}  # outer lines of each staff


def ledger_lines(p: pitch.Pitch, which: str) -> int:
    """Ledger lines a note needs on a treble or bass staff: every 2 diatonic steps past an outer
    line adds one (C4 on treble: 1; D4 hangs below the staff: 0)."""
    bottom, top = (pitch.Pitch(x).diatonicNoteNum for x in STAFF_LINES[which])
    return max(bottom - p.diatonicNoteNum, p.diatonicNoteNum - top, 0) // 2


def choose_clefs(part: stream.Part, home: str, switch: float = 8.0, away: float = 1.0) -> list[str]:
    """Clef for every bar of a staff, chosen globally (dynamic programming over the bars).

    Minimizes the ledger lines of all notes, plus `switch` per clef change (a change must save
    more than that many ledger lines to be worth it) and `away` per bar in the other clef than
    the staff's `home` (a tie-breaker). The first bar's clef is free, so a low opening starts in
    bass clef instead of switching after one bar.
    """
    clefs = ("treble", "bass")
    measures = list(part.getElementsByClass(stream.Measure))
    cost, back = {c: 0.0 for c in clefs}, []
    for i, m in enumerate(measures):
        pitches = [p for n in m.recurse().notes for p in n.pitches]
        here = {c: sum(ledger_lines(p, c) for p in pitches) + (away if c != home else 0.0) for c in clefs}
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


def add_clef_changes(part: stream.Part, home: str) -> None:
    """Write the clefs from `choose_clefs`: the first bar's, then only where the clef changes.

    Never mid-sustain: when a note is tied across the barline where the clef changes (pop
    syncopation does this in over half the bars), the change moves back to just before that
    note starts, as engravers do, so the whole held note sits in one clef.
    """
    measures = list(part.getElementsByClass(stream.Measure))
    previous = None
    for i, (m, c) in enumerate(zip(measures, choose_clefs(part, home))):
        if m.clef is not None:
            m.remove(m.clef)
        if c != previous:
            new = clef.TrebleClef() if c == "treble" else clef.BassClef()
            held = [n for n in measures[i - 1].recurse().notes
                    if n.tie is not None and n.tie.type == "start"] if i > 0 else []
            tied_in = any(n.tie is not None and n.tie.type in ("stop", "continue")
                          for n in m.recurse().notes if n.offset == 0)
            if tied_in and held:
                measures[i - 1].insert(min(n.offset for n in held), new)
            else:
                m.insert(0, new)
        previous = c


def write_musicxml(score: stream.Score, path: Path) -> None:
    """Export, then give every mid-score clef change its staff number.

    music21 joins the two PartStaffs into one two-staff part but writes clef *changes* as a bare
    <clef>. MusicXML reads that as staff 1, yet musicxml2ly may put it on the other staff (a
    right-hand change turned the left hand into treble clef). A clef belongs to the staff of the
    next note after it.
    """
    score.write("musicxml", fp=path)
    text = path.read_text()
    header = text[: text.index("<score-partwise")]  # keep the XML declaration and DOCTYPE
    root = ET.fromstring(text[len(header):])
    for measure in root.iter("measure"):
        children = list(measure)
        for i, el in enumerate(children):
            if el.tag != "attributes":
                continue
            staff = next((n.findtext("staff") for n in children[i + 1:] if n.tag == "note"), "1") or "1"
            for c in el.findall("clef"):
                c.attrib.setdefault("number", staff)
    path.write_text(header + ET.tostring(root, encoding="unicode"))
