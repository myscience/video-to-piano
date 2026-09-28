"""Keys, spelling and a MusicXML writer: the score's last step, without music21.

The pipeline already knows every note as integer ticks on a clean grid; turning that into
MusicXML directly (notatable values, ties, beams, printed accidentals, voices, clefs) takes a few
milliseconds, where music21's general-purpose measure building and export took ~3.5 s and made
every edit preview wait for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from xml.sax.saxutils import escape

import numpy as np

TICKS = 12  # per quarter note (MusicXML <divisions>)
LETTERS = "CDEFGAB"
NATURAL = (0, 2, 4, 5, 7, 9, 11)
SHARP_ORDER = "FCGDAEB"

# Krumhansl-Kessler key profiles (probe-tone ratings), C major / C minor.
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
# Key signature of each major tonic; the flat side where both exist (Gb over F#, Db over C#).
MAJOR_SHARPS = {0: 0, 7: 1, 2: 2, 9: 3, 4: 4, 11: 5, 6: -6, 1: -5, 8: -4, 3: -3, 10: -2, 5: -1}


@dataclass(frozen=True)
class Key:
    tonic: int  # pitch class
    mode: str  # "major" | "minor"

    @property
    def sharps(self) -> int:
        """Key signature: >0 sharps, <0 flats (a minor key shares its relative major's)."""
        return MAJOR_SHARPS[(self.tonic + (3 if self.mode == "minor" else 0)) % 12]

    @property
    def scale(self) -> set[int]:
        steps = (0, 2, 4, 5, 7, 9, 11) if self.mode == "major" else (0, 2, 3, 5, 7, 8, 10)
        return {(self.tonic + s) % 12 for s in steps}

    @property
    def name(self) -> str:
        letter, alter, _ = spell(60 + self.tonic, self.sharps)
        return f"{LETTERS[letter]}{'♯' * alter if alter > 0 else '♭' * -alter} {self.mode}"


def estimate_key(pitches: list[int], weights: list[float]) -> Key:
    """Krumhansl-Schmuckler: correlate the duration-weighted pitch-class histogram with every
    rotation of the major and minor profiles."""
    hist = np.zeros(12)
    for p, w in zip(pitches, weights):
        hist[p % 12] += max(w, 0.01)
    best = max(((np.corrcoef(hist, np.roll(profile, tonic))[0, 1], tonic, mode)
                for mode, profile in (("major", KK_MAJOR), ("minor", KK_MINOR)) for tonic in range(12)))
    return Key(best[1], best[2])


def key_alters(sharps: int) -> list[int]:
    """Alteration of each letter C..B in the key signature."""
    alters = [0] * 7
    order = SHARP_ORDER if sharps > 0 else SHARP_ORDER[::-1]
    for letter in order[: abs(sharps)]:
        alters[LETTERS.index(letter)] = 1 if sharps > 0 else -1
    return alters


@lru_cache(maxsize=4096)
def spell(midi: int, sharps: int) -> tuple[int, int, int]:
    """(letter index C=0..B=6, alter, octave) the way the key would write it: scale notes by the
    key's own names (B♭, not A♯, in G♭ major), chromatic notes leaning like the signature."""
    pc, alters = midi % 12, key_alters(sharps)
    letter = next((i for i in range(7) if (NATURAL[i] + alters[i]) % 12 == pc), None)
    if letter is not None:
        alter = alters[letter]
    elif pc in NATURAL:
        letter, alter = NATURAL.index(pc), 0
    elif sharps > 0:
        letter, alter = NATURAL.index(pc - 1), 1
    else:
        letter, alter = NATURAL.index((pc + 1) % 12), -1
    return letter, alter, (midi - NATURAL[letter] - alter) // 12 - 1


def diatonic(midi: int, sharps: int) -> int:
    """Staff position: 7 per octave, C0 = 0."""
    letter, _, octave = spell(midi, sharps)
    return octave * 7 + letter


# ---- written structure ---------------------------------------------------------------------

NOTATABLE = [(48, "whole", 0), (36, "half", 1), (24, "half", 0), (18, "quarter", 1), (12, "quarter", 0),
             (9, "eighth", 1), (6, "eighth", 0), (3, "16th", 0)]
TYPE_OF = {t: (name, dots) for t, name, dots in NOTATABLE}


@dataclass
class Piece:
    """One written note, chord or rest inside a bar (a notatable length)."""

    start: int  # ticks from the start of the bar
    length: int
    pitches: tuple[int, ...] = ()  # empty: rest
    tie_start: bool = False  # tied to the next piece
    tie_stop: bool = False  # tied from the previous piece
    hidden: bool = False  # an invisible rest (a secondary voice dropping out)


@dataclass
class Voice:
    number: int  # MusicXML voice: staff 1 -> 1, 2; staff 2 -> 5, 6
    pieces: list[Piece]
    stem: str | None = None  # "up" | "down" when two voices share the staff


@dataclass
class Bar:
    voices: list[Voice]
    clef: str | None = None  # clef change ("treble" | "bass") in this bar
    clef_at: int = 0  # ticks from the bar start where the change sits


@dataclass
class Staff:
    number: int  # 1 = upper (right hand), 2 = lower (left hand)
    bars: list[Bar] = field(default_factory=list)


def split_notatable(start: int, length: int) -> list[tuple[int, int]]:
    """Cut a length into notatable values (largest first): 15 ticks -> quarter + 16th."""
    out = []
    while length > 0:
        t = next(t for t, _, _ in NOTATABLE if t <= length)
        out.append((start, t))
        start += t
        length -= t
    return out


def pieces_in_bars(events: list[tuple[int, int, list[int]]], bar: int, n_bars: int,
                   hidden_rests: bool = False) -> list[list[Piece]]:
    """A voice's events (start, length, pitches; score ticks) -> notatable pieces per bar, with
    rests filling every gap and ties across barlines and within split notes."""
    timeline, t = [], 0
    for s, length, pitches in sorted(events):
        if s > t:
            timeline.append((t, s - t, ()))
        timeline.append((s, length, tuple(sorted(pitches))))
        t = max(t, s + length)
    if t < n_bars * bar:
        timeline.append((t, n_bars * bar - t, ()))
    bars: list[list[Piece]] = [[] for _ in range(n_bars)]
    for s, length, pitches in timeline:
        first, end = s, s + length
        while s < end:
            b = s // bar
            if b >= n_bars:
                break
            piece_end = min(end, (b + 1) * bar)
            parts = split_notatable(s - b * bar, piece_end - s)
            for k, (ps, pl) in enumerate(parts):
                bars[b].append(Piece(ps, pl, pitches,
                                     tie_start=bool(pitches) and (b * bar + ps + pl < end),
                                     tie_stop=bool(pitches) and (b * bar + ps > first),
                                     hidden=hidden_rests and not pitches))
            s = piece_end
    return bars


# ---- writing -------------------------------------------------------------------------------

ACCIDENTALS = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
CLEF_SIGN = {"treble": ("G", 2), "bass": ("F", 4)}


def _beams(pieces: list[Piece]) -> list[list[tuple[int, str]]]:
    """MusicXML beams per piece: eighths and 16ths grouped within each beat."""
    beams: list[list[tuple[int, str]]] = [[] for _ in pieces]
    i = 0
    while i < len(pieces):
        p = pieces[i]
        if not p.pitches or p.length >= TICKS or p.hidden:
            i += 1
            continue
        beat, j = p.start // TICKS, i
        while (j + 1 < len(pieces) and pieces[j + 1].pitches and pieces[j + 1].length < TICKS
               and pieces[j + 1].start // TICKS == beat and (pieces[j + 1].start + pieces[j + 1].length - 1) // TICKS == beat):
            j += 1
        if (p.start + p.length - 1) // TICKS == beat and j > i:
            group = list(range(i, j + 1))
            for k, idx in enumerate(group):
                beams[idx].append((1, "begin" if k == 0 else "end" if k == len(group) - 1 else "continue"))
            k = 0
            while k < len(group):  # secondary beams: runs of 16ths inside the group
                if pieces[group[k]].length != 3:
                    k += 1
                    continue
                m = k
                while m + 1 < len(group) and pieces[group[m + 1]].length == 3:
                    m += 1
                if m > k:
                    for r in range(k, m + 1):
                        beams[group[r]].append((2, "begin" if r == k else "end" if r == m else "continue"))
                else:
                    beams[group[k]].append((2, "forward hook" if k == 0 else "backward hook"))
                k = m + 1
            i = j + 1
        else:
            i += 1
    return beams


def _accidentals(bar: Bar, sharps: int) -> dict[tuple[int, int, int], str]:
    """Printed accidentals for one staff's bar: {(voice, piece index, pitch): accidental}.
    A note shows one when it differs from the key signature, or from an earlier accidental on the
    same line in this bar; tied continuations never do."""
    alters = key_alters(sharps)
    state: dict[tuple[int, int], int] = {}
    notes = sorted(((p.start, v.number, i, pitch, p.tie_stop) for v in bar.voices
                    for i, p in enumerate(v.pieces) for pitch in p.pitches))
    printed = {}
    for _, vnum, i, pitch, tied in notes:
        letter, alter, octave = spell(pitch, sharps)
        current = state.get((letter, octave), alters[letter])
        if not tied and alter != current:
            printed[(vnum, i, pitch)] = ACCIDENTALS[alter]
        state[(letter, octave)] = alter
    return printed


def _clef(number: int, which: str) -> str:
    sign, line = CLEF_SIGN[which]
    return f'<clef number="{number}"><sign>{sign}</sign><line>{line}</line></clef>'


def _note(p: Piece, i: int, pitch: int | None, chord: bool, voice: Voice, staff: int, sharps: int,
          accidental: str | None, beams: list[tuple[int, str]], bar_len: int, bar_start: int = 0) -> str:
    # Notes carry their own id, "n<pitch>t<tick>v<voice>": Verovio keeps it in the SVG and the
    # timemap, so the viewer learns each note's pitch without asking Verovio note by note.
    out = [f'<note id="n{pitch}t{bar_start + p.start}v{voice.number}">' if pitch is not None else "<note>"]
    if chord:
        out.append("<chord/>")
    if pitch is None:
        out.append('<rest measure="yes"/>' if p.length == bar_len and p.start == 0 else "<rest/>")
    else:
        letter, alter, octave = spell(pitch, sharps)
        out.append(f"<pitch><step>{LETTERS[letter]}</step>" + (f"<alter>{alter}</alter>" if alter else "") +
                   f"<octave>{octave}</octave></pitch>")
    out.append(f"<duration>{p.length}</duration>")
    if pitch is not None:
        if p.tie_stop:
            out.append('<tie type="stop"/>')
        if p.tie_start:
            out.append('<tie type="start"/>')
    out.append(f"<voice>{voice.number}</voice>")
    if not (pitch is None and p.length == bar_len and p.start == 0):
        name, dots = TYPE_OF[p.length]
        out.append(f"<type>{name}</type>" + "<dot/>" * dots)
    if accidental:
        out.append(f"<accidental>{accidental}</accidental>")
    if pitch is not None and voice.stem:
        out.append(f"<stem>{voice.stem}</stem>")
    out.append(f"<staff>{staff}</staff>")
    if not chord:
        out.extend(f'<beam number="{n}">{kind}</beam>' for n, kind in beams)
    if pitch is not None and (p.tie_start or p.tie_stop):
        ties = ('<tied type="stop"/>' if p.tie_stop else "") + ('<tied type="start"/>' if p.tie_start else "")
        out.append(f"<notations>{ties}</notations>")
    out.append("</note>")
    return "".join(out)


def write(staves: list[Staff], key: Key, beats_per_bar: int, bpm: float, title: str, composer: str,
          first_clefs: dict[int, str]) -> str:
    """Two staves of bars -> a MusicXML (partwise 4.0) document."""
    bar_len = beats_per_bar * TICKS
    sharps = key.sharps
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
             '"http://www.musicxml.org/dtds/partwise.dtd">',
             '<score-partwise version="4.0">',
             f"<movement-title>{escape(title)}</movement-title>",  # (a work-title too shows twice)
             "<identification>" + (f'<creator type="composer">{escape(composer)}</creator>' if composer else "") +
             "<encoding><software>pianoscribe</software></encoding></identification>",
             '<part-list><score-part id="P1"><part-name></part-name></score-part></part-list>',
             '<part id="P1">']
    n_bars = len(staves[0].bars)
    for b in range(n_bars):
        lines.append(f'<measure number="{b + 1}">')
        if b == 0:
            lines.append(f"<attributes><divisions>{TICKS}</divisions>"
                         f"<key><fifths>{sharps}</fifths><mode>{key.mode}</mode></key>"
                         f"<time><beats>{beats_per_bar}</beats><beat-type>4</beat-type></time>"
                         "<staves>2</staves><part-symbol>brace</part-symbol>" +
                         "".join(_clef(s.number, first_clefs[s.number]) for s in staves) + "</attributes>")
            lines.append('<direction placement="above"><direction-type><metronome><beat-unit>quarter</beat-unit>'
                         f"<per-minute>{round(bpm)}</per-minute></metronome></direction-type>"
                         f'<staff>1</staff><sound tempo="{round(bpm)}"/></direction>')
        voices_written = 0
        total_voices = sum(len(s.bars[b].voices) for s in staves)
        for staff in staves:
            bar = staff.bars[b]
            printed = _accidentals(bar, sharps)
            for vi, voice in enumerate(bar.voices):
                beams = _beams(voice.pieces)
                clef_pending = bar.clef if vi == 0 else None
                for i, p in enumerate(voice.pieces):
                    if clef_pending and p.start >= bar.clef_at:
                        lines.append(f"<attributes>{_clef(staff.number, clef_pending)}</attributes>")
                        clef_pending = None
                    if p.hidden:
                        lines.append(f"<forward><duration>{p.length}</duration><voice>{voice.number}</voice>"
                                     f"<staff>{staff.number}</staff></forward>")
                        continue
                    if not p.pitches:
                        lines.append(_note(p, i, None, False, voice, staff.number, sharps, None, beams[i], bar_len))
                        continue
                    for k, pitch in enumerate(p.pitches):
                        lines.append(_note(p, i, pitch, k > 0, voice, staff.number, sharps,
                                           printed.get((voice.number, i, pitch)), beams[i], bar_len, b * bar_len))
                voices_written += 1
                if voices_written < total_voices:
                    lines.append(f"<backup><duration>{bar_len}</duration></backup>")
        if b == n_bars - 1:
            lines.append('<barline location="right"><bar-style>light-heavy</bar-style></barline>')
        lines.append("</measure>")
    lines += ["</part>", "</score-partwise>"]
    return "\n".join(lines)
