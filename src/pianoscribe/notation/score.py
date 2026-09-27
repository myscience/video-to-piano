"""Quantized notes with hands -> a two-staff piano score (music21) -> MusicXML.

First version, deliberately simple: one voice per hand. Notes starting together form a chord,
each chord lasts until that hand's next chord unless a clear silence follows (`written_end`).
Held notes under a moving line are cut at the next chord (voices will fix that).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import replace
from fractions import Fraction
from itertools import groupby
from pathlib import Path

from music21 import chord, clef, key, layout, meter, metadata, note, pitch, stream, tempo

from ..rhythm.quantize import TICKS_PER_BEAT, QuantizedNote
from .hands import MAX_SPAN
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
        if nxt is not None:
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


def voiced_bars(upper: list, lower: list, bar: int, n_bars: int, seed: list[bool] | None = None) -> list[bool]:
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
    spans = [(s // bar, (s + length - 1) // bar) for s, length, p in upper + lower if p]
    changed = True
    while changed:
        changed = False
        for a, b in spans:
            if b > a and any(need[a : b + 1]) and not all(need[a : b + 1]):
                need[a : b + 1] = [True] * (b - a + 1)
                changed = True
    return need


def _element(pitches: list[int], length: int, k: key.Key, stem: str | None = None) -> note.GeneralNote:
    ql = Fraction(length, TICKS_PER_BEAT)
    if not pitches:
        return note.Rest(quarterLength=ql)
    el = (note.Note(spell(pitches[0], k), quarterLength=ql) if len(pitches) == 1
          else chord.Chord([spell(p, k) for p in pitches], quarterLength=ql))
    if stem:
        el.stemDirection = stem
    return el


def _measures(events: list, k: key.Key, ts: meter.TimeSignature, total: int, stem: str | None = None,
              hide_rests: bool = False) -> list[stream.Measure]:
    """Events (score ticks) -> measures with rests filled in and ties across barlines.
    hide_rests: rests keep their time but aren't printed (a secondary voice dropping out)."""
    s = stream.Stream()
    s.insert(0, meter.TimeSignature(ts.ratioString))
    for start, length, pitches in events:
        s.insert(Fraction(start, TICKS_PER_BEAT), _element(pitches, length, k, stem))
    s.makeRests(refStreamOrTimeRange=[0, Fraction(total, TICKS_PER_BEAT)], fillGaps=True, inPlace=True)
    measured = s.makeMeasures()
    measured.makeTies(inPlace=True)
    if hide_rests:
        for r in measured.recurse().getElementsByClass(note.Rest):
            r.style.hideObjectOnPrint = True
    return list(measured.getElementsByClass(stream.Measure))


def cut_out_of_reach(events: list, hand: list[QuantizedNote], reach: int = MAX_SPAN) -> list:
    """End a held note as soon as the same hand plays something beyond an octave from it: one
    hand can't hold it and play there (the remainder becomes a rest)."""
    out = []
    for s, length, pitches in events:
        if pitches:
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


def build_part(notes: list[QuantizedNote], hand: Hand, k: key.Key, ts: meter.TimeSignature, total: int,
               pedal: list[tuple[int, int]], voices: bool = True,
               min_gain: float = 1.0, per_rest: float = 0.5) -> tuple[stream.PartStaff, int]:
    """One staff. Returns it with the number of bars that use two voices.

    A bar gets two voices only where they are worth it: the sustain they show (that one voice
    would cut) is at least `min_gain` beats and at least `per_rest` beats per visible rest they
    add. The secondary voice (right-hand accompaniment, left-hand notes above the bass) hides its
    rests; the melody and the bass keep theirs, which carry musical meaning.
    """
    bar = round(ts.barDuration.quarterLength * TICKS_PER_BEAT)
    n_bars = -(-total // bar)
    merged = _measures(hand_events(notes, pedal), k, ts, total)
    need = [False] * n_bars
    if voices:
        upper_notes, lower_notes = split_roles(notes, hand)
        upper = cut_out_of_reach(hand_events(upper_notes, pedal, TICKS_PER_BEAT), notes)
        lower = cut_out_of_reach(hand_events(lower_notes, pedal, TICKS_PER_BEAT), notes)
        ups = _measures(upper, k, ts, total, "up", hide_rests=hand == "L")
        lows = _measures(lower, k, ts, total, "down", hide_rests=hand == "R")
        onsets = sorted({n.start for n in notes})
        gain = [a + b for a, b in zip(sustain_gain(upper, onsets, bar, n_bars), sustain_gain(lower, onsets, bar, n_bars))]
        primary = lows if hand == "L" else ups
        rests = [len(primary[i].getElementsByClass(note.Rest)) for i in range(n_bars)]
        worth = [gain[i] >= min_gain * TICKS_PER_BEAT and gain[i] >= per_rest * TICKS_PER_BEAT * rests[i]
                 for i in range(n_bars)]
        diverge = voiced_bars(upper, lower, bar, n_bars)
        need = voiced_bars(upper, lower, bar, n_bars, [d and w for d, w in zip(diverge, worth)])

    part = stream.PartStaff()
    for i in range(n_bars):
        if need[i]:
            m = stream.Measure(number=i + 1)
            for vid, src in (("1", ups[i]), ("2", lows[i])):
                v = stream.Voice(id=vid)
                for el in src.notesAndRests:
                    v.insert(el.offset, el)
                m.insert(0, v)
        else:
            m = merged[i]
            m.number = i + 1
        part.append(m)
    first = part.getElementsByClass(stream.Measure).first()
    for old in list(first.getElementsByClass((clef.Clef, key.KeySignature, meter.TimeSignature))):
        first.remove(old)
    first.insert(0, clef.TrebleClef() if hand == "R" else clef.BassClef())
    first.insert(0, key.Key(k.tonic.name, k.mode))
    first.insert(0, meter.TimeSignature(ts.ratioString))
    # Print only the accidentals the key signature doesn't already imply (bars are assembled by
    # hand, so music21's usual makeNotation pass doesn't run: every G-flat got a flat).
    part.makeAccidentals(inPlace=True)
    return part, sum(need)


def build_score(notes: list[QuantizedNote], hands: list[Hand], beats_per_bar: int, bpm: float,
                title: str = "", composer: str = "", pedal: list[tuple[int, int]] = (),
                voices: bool = True) -> stream.Score:
    """pedal: sustain-pedal (down, up) intervals in ticks, on the same grid as the notes.
    voices: split each hand into melody/accompaniment (right) or bass/upper (left) where they
    move independently; otherwise one voice of chords per hand."""
    k = estimate_key([n.pitch for n in notes], [n.duration / TICKS_PER_BEAT for n in notes])
    bar = beats_per_bar * TICKS_PER_BEAT
    shift = -(min(n.start for n in notes) // bar) * bar  # first note lands in bar 1 (drops empty bars)
    notes = [replace(n, start=n.start + shift, end=n.end + shift) for n in notes]
    pedal = [(a + shift, b + shift) for a, b in pedal]
    total = -(-max(n.end for n in notes) // bar) * bar
    ts = meter.TimeSignature(f"{beats_per_bar}/4")

    score = stream.Score()
    # Only the movement title: music21 would also copy `title` into it (shown twice as a subtitle).
    score.metadata = metadata.Metadata(movementName=title, composer=composer)
    staves = []
    for hand in ("R", "L"):
        part, n_voiced = build_part([n for n, h in zip(notes, hands) if h == hand], hand, k, ts, total, pedal, voices)
        part.id = f"{hand}H"
        score.voiced_bars = getattr(score, "voiced_bars", {}) | {hand: n_voiced}
        staves.append(part)
        score.insert(0, part)
    staves[0].getElementsByClass(stream.Measure).first().insert(0, tempo.MetronomeMark(number=round(bpm)))
    score.shift_beats = shift / TICKS_PER_BEAT  # score position = beat position + shift_beats
    score.insert(0, layout.StaffGroup(staves, symbol="brace", barTogether=True))
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
    """Export, then fix two things music21 leaves ambiguous in the joined two-staff part.

    Clefs: clef *changes* come out as a bare <clef>. MusicXML reads that as staff 1, yet
    musicxml2ly may put it on the other staff (a right-hand change turned the left hand into
    treble clef). A clef belongs to the staff of the next note after it.
    Voices: both staves reuse voice numbers 1, 2, ..., and musicxml2ly groups notes by voice
    number across the whole part, merging the hands (barlines vanished, staves drifted apart).
    Each staff gets its own range, as notation programs do: staff 1 voices 1-4, staff 2 voices 5-8
    (per bar, the n-th voice to appear in a staff becomes base + n).
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
        seen: dict[str, list[str]] = {}
        for el in children:
            v, st = el.find("voice"), el.findtext("staff") or "1"
            if el.tag in ("note", "forward") and v is not None:
                order = seen.setdefault(st, [])
                if v.text not in order:
                    order.append(v.text)
                v.text = str((1 if st == "1" else 5) + order.index(v.text))
    path.write_text(header + ET.tostring(root, encoding="unicode"))
