"""User corrections, kept apart from what the pipeline computes so they survive every rebuild.

Edits live in `score/edits.json` and are replayed in order on top of the quantized notes each
time the score is built. Each one targets a note by `{"pitch": p, "tick": t}`: the note of that
pitch sounding at tick t (ticks from the first downbeat, like QuantizedNote.start), as it stands
after the edits before it. Clicking any part of a tied note therefore finds the whole note.

    {"op": "pitch",  "target": T, "delta": +1}          semitones
    {"op": "hand",   "target": T, "hand": "L"}           also steers the hand decoder around it
    {"op": "length", "target": T, "delta": +3}           ticks; the written length is then kept as is
    {"op": "move",   "target": T, "delta": -3}           ticks; the whole note moves
    {"op": "delete", "target": T}
    {"op": "add",    "pitch": 64, "start": 480, "end": 486, "hand": "R"}
    {"op": "transpose", "semitones": 2}              every note, from here on in the sequence

Score-wide settings (the last of each kind wins; see `settings`):

    {"op": "key",      "tonic": 8, "mode": "major"}   key signature and spelling (same notes)
    {"op": "key",      "auto": true}                  back to the estimated key
    {"op": "tempo",    "bpm": 116}                    the printed tempo mark
    {"op": "beat",     "factor": 2}                   2: the tracker found half speed; 0.5: double
    {"op": "meter",    "beats": 3}                    beats per bar
    {"op": "downbeat", "shift": 1}                    move every barline by whole beats
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..rhythm.quantize import TICKS_PER_BEAT, QuantizedNote
from ..transcribe.base import Hand

STEP = TICKS_PER_BEAT // 4  # a 16th: the grid every edit moves on
OPS = {"pitch", "hand", "length", "move", "delete", "add", "transpose"}
SETTINGS = {"key", "tempo", "beat", "meter", "downbeat"}


def settings(edits: list[dict]) -> dict:
    """The score-wide settings in effect: {"key": (tonic, mode) | None, "bpm": float | None,
    "beat": factor, "meter": beats | None, "downbeat": shift, "transpose": total semitones}."""
    out = {"key": None, "bpm": None, "beat": 1.0, "meter": None, "downbeat": 0, "transpose": 0}
    for e in edits:
        op = e.get("op")
        if op == "key":
            out["key"] = None if e.get("auto") else (int(e["tonic"]) % 12, e.get("mode", "major"))
        elif op == "tempo":
            out["bpm"] = float(e["bpm"]) if e.get("bpm") else None
        elif op == "beat":
            out["beat"] = float(e["factor"])
        elif op == "meter":
            out["meter"] = int(e["beats"]) if e.get("beats") else None
        elif op == "downbeat":
            out["downbeat"] = int(e["shift"])
        elif op == "transpose":
            out["transpose"] += int(e["semitones"])
    return out


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []


def save(path: Path, edits: list[dict]) -> None:
    path.write_text(json.dumps(edits, indent=1))


def resolve(notes: list[QuantizedNote], target: dict) -> int | None:
    """Index of the note a target points at: that pitch, sounding at that tick (exact start first)."""
    pitch, tick = int(target["pitch"]), int(target["tick"])
    sounding = [i for i, n in enumerate(notes) if n.pitch == pitch and n.start <= tick < n.end]
    exact = [i for i in sounding if notes[i].start == tick]
    return (exact or sounding or [None])[0]


def apply(notes: list[QuantizedNote], edits: list[dict]) -> tuple[list[QuantizedNote], dict[int, Hand], list[dict]]:
    """Replay `edits` on `notes`. Returns (notes, forced hands by index in those notes, skipped edits).

    An edit whose target no longer exists (e.g. after re-transcribing) is skipped, not an error.
    """
    work: list[list] = [[n, None] for n in notes]  # [note, forced hand]
    skipped = []
    for e in edits:
        op = e.get("op")
        if op in SETTINGS:  # score-wide, handled by `settings`
            continue
        if op == "transpose":
            d = int(e["semitones"])
            work = [[replace(n, pitch=min(108, max(21, n.pitch + d))), h] for n, h in work]
            continue
        if op == "add":
            start, end = int(e["start"]), int(e["end"])
            hand = e.get("hand")
            work.append([QuantizedNote(int(e["pitch"]), start, max(end, start + STEP), 80, hand, True), hand])
            continue
        i = resolve([w[0] for w in work], e.get("target", {})) if op in OPS else None
        if i is None:
            skipped.append(e)
            continue
        n = work[i][0]
        if op == "pitch":
            work[i][0] = replace(n, pitch=min(108, max(21, n.pitch + int(e["delta"]))))
        elif op == "hand":
            work[i][1] = e["hand"]
        elif op == "length":
            work[i][0] = replace(n, end=max(n.start + STEP, n.end + int(e["delta"])), locked=True)
        elif op == "move":
            d = int(e["delta"])
            work[i][0] = replace(n, start=n.start + d, end=n.end + d)
        elif op == "delete":
            work.pop(i)
    # Two notes of the same pitch starting together are one key: keep the first.
    seen, out = set(), []
    for n, hand in sorted(work, key=lambda w: (w[0].start, w[0].pitch)):
        if (n.pitch, n.start) not in seen:
            seen.add((n.pitch, n.start))
            out.append((n, hand))
    return [n for n, _ in out], {i: h for i, (_, h) in enumerate(out) if h}, skipped
