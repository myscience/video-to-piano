"""The whole pipeline as plain functions, shared by the CLI, the viewer's "add song" and edits.

    add_song:  fetch -> transcribe (+ merge) -> beats -> build
    build:     notes + beats + edits -> score.musicxml, <slug>.pdf, viewer files
    preview:   the same score with pending (unsaved) edits, rendered for the viewer only
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .library import LIBRARY, Song

Progress = Callable[[str, str], None]  # (step, message)
PLAYABLE = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".webm", ".mp4"}
# music21 and Verovio keep global state: one score build or render at a time (the viewer's
# server runs requests in threads, and a background "add song" job may be building too).
_BUILD = threading.RLock()


def _quiet(step: str, message: str) -> None:
    pass


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "song"


# ---- steps ---------------------------------------------------------------------------------

def fetch(song: Song, source: str, progress: Progress = _quiet) -> None:
    """A URL (YouTube, anything yt-dlp reads) or a local audio/video file -> source/audio.wav."""
    from .sources.audio import extract_wav

    if re.match(r"^https?://", source):
        from .sources.youtube import download_audio

        progress("fetch", "downloading audio")
        download_audio(source, song)
        return
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(source)
    progress("fetch", f"importing {path.name}")
    if path.suffix.lower() in PLAYABLE:  # keep the original for playback in the viewer
        shutil.copyfile(path, song.audio.parent / f"original{path.suffix.lower()}")  # creates source/
    extract_wav(path, song.audio)
    song.update_meta(title=path.stem, source_file=str(path.resolve()))


def transcribe(song: Song, device: str = "auto", backends: tuple[str, ...] = ("transkun", "bytedance"),
               progress: Progress = _quiet) -> dict[str, int]:
    """Audio -> notes/<backend>.mid for each backend, then notes/ensemble.mid if both ran."""
    from .sources.audio import load_audio
    from .transcribe import get_transcriber
    from .transcribe.base import Transcription
    from .transcribe.ensemble import merge

    counts = {}
    for name in backends:
        progress("transcribe", f"listening with {name}")
        model = get_transcriber(name, device)
        result = model.transcribe(load_audio(song.audio, model.sample_rate))
        result.save(song.notes(name))
        counts[name] = len(result.notes)
    if {"transkun", "bytedance"} <= set(backends):
        merged = merge(Transcription.load(song.notes("transkun")), Transcription.load(song.notes("bytedance")))
        merged.save(song.notes("ensemble"))
        counts["ensemble"] = len(merged.notes)
    return counts


def beats(song: Song, device: str = "auto", meter: int | None = None, tightness: float = 300.0,
          recompute: bool = False, progress: Progress = _quiet):
    """Audio -> rhythm/beats.json. Returns (BeatGrid, BarChoice)."""
    from .rhythm.beats import Activations, beat_activations, track_beats
    from .sources.audio import load_audio

    progress("beats", "finding the beat")
    if song.activations.exists() and not recompute:
        act = Activations.load(song.activations)
    else:
        act = beat_activations(load_audio(song.audio, 22_050), 22_050, device)
        act.save(song.activations)
    grid, bars = track_beats(act, meter, tightness)
    grid.save(song.beats)
    return grid, bars


# ---- building the score --------------------------------------------------------------------

@dataclass
class Built:
    key: str
    notes: int
    voiced: dict
    span: tuple[float, float]
    pages: int = 0
    skipped_edits: list = field(default_factory=list)
    seconds: float = 0.0


def default_notes(song: Song) -> str:
    available = song.all_notes()
    return "ensemble" if "ensemble" in available else next(iter(available), "ensemble")


def _score(song: Song, notes_name: str | None, pending: list[dict], voices: bool,
           title: str | None, composer: str | None):
    from .notation import edits as E
    from .notation.hands import decode_hands
    from .notation.score import build_score, song_span
    from .rhythm.beats import BeatGrid
    from .rhythm.quantize import quantize, quantize_pedal
    from .transcribe.base import Transcription

    from .notation.musicxml import Key
    from .rhythm.beats import transform

    edits = E.load(song.edits) + list(pending)
    st = E.settings(edits)
    grid = transform(BeatGrid.load(song.beats), st["beat"], st["meter"], st["downbeat"])
    t = Transcription.load(song.notes(notes_name or default_notes(song)))
    start, end = song_span(t)
    t = Transcription([n for n in t.notes if start <= n.onset <= end], t.pedal)
    q, forced, skipped = E.apply(quantize(t, grid), edits)
    guess_title, guess_composer = song.credits()
    eng = build_score(q, decode_hands(q, forced=forced), grid.beats_per_bar, st["bpm"] or grid.tempo,
                      title or guess_title, composer or guess_composer, pedal=quantize_pedal(t, grid),
                      voices=voices, key=Key(*st["key"]) if st["key"] else None)
    eng.settings = st | {"bpm_mark": round(st["bpm"] or grid.tempo), "meter": grid.beats_per_bar}
    info = Built(eng.key.name, len(q), eng.voiced, (start, end), skipped_edits=skipped)
    return eng, grid, info


def _sync(song: Song, grid, eng, pages: int) -> dict:
    """How the viewer maps score positions (quarters from bar 1) to recording time."""
    title, composer = song.credits()
    return {"key_sharps": eng.key.sharps, "key": eng.key.name, "key_tonic": eng.key.tonic, "key_mode": eng.key.mode,
            "settings": getattr(eng, "settings", {}), "beats": [round(float(b), 4) for b in grid.beats],
            "first_downbeat": int(np.searchsorted(grid.beats, grid.downbeats[0])),
            "shift_beats": eng.shift_beats, "beats_per_bar": grid.beats_per_bar, "bpm": round(grid.tempo, 2),
            "pages": pages, "title": title, "composer": composer}


def build(song: Song, notes_name: str | None = None, pdf: bool = True, png: bool = False, view: bool = True,
          voices: bool = True, title: str | None = None, composer: str | None = None,
          progress: Progress = _quiet) -> Built:
    """Notes + beats + saved edits -> score/score.musicxml, <slug>.pdf and score/view/."""
    with _BUILD:
        return _build(song, notes_name, pdf, png, view, voices, title, composer, progress)


def _build(song, notes_name, pdf, png, view, voices, title, composer, progress) -> Built:
    from .render.lilypond import musicxml_to_pdf
    from .render.verovio import render_view

    t0 = time.perf_counter()
    if title or composer:  # explicit credits are remembered for every later build and the library
        song.update_meta(song_title=title or song.credits()[0], composer=composer or song.credits()[1])
    progress("score", "writing the score")
    eng, grid, info = _score(song, notes_name, [], voices, title, composer)
    song.score.write_text(eng.xml)
    if pdf:
        progress("score", "engraving the PDF")
        musicxml_to_pdf(song.score, song.pdf, png=png)
    if view:
        progress("score", "preparing the viewer")
        info.pages = render_view(song.score, song.view_dir)
        (song.view_dir / "sync.json").write_text(json.dumps(_sync(song, grid, eng, info.pages)))
    info.seconds = time.perf_counter() - t0
    return info


def preview(song: Song, pending: list[dict], line: bool = False) -> dict:
    """The score with saved + pending edits, rendered for the viewer only (nothing is written)."""
    from .render.verovio import render

    with _BUILD:
        eng, grid, info = _score(song, None, pending, True, None, None)
        view = render(eng.xml, line=line)
    view["sync"] = _sync(song, grid, eng, len(view["pages"]))
    view["skipped"] = info.skipped_edits
    return view


def add_song(source: str, slug: str | None = None, title: str | None = None, composer: str | None = None,
             device: str = "auto", library: Path = LIBRARY, progress: Progress = _quiet) -> Song:
    """Everything from a URL or file to a playable score in the library."""
    if not slug:
        if title:
            slug = slugify(title)
        elif re.match(r"^https?://", source):
            from .sources.youtube import probe

            progress("fetch", "looking up the video")
            slug = slugify(probe(source)["title"].split("|")[0])
        else:
            slug = slugify(Path(source).stem)
    song = Song.get(slug, library)
    fetch(song, source, progress)
    if not title and not composer:  # guess credits from the downloaded title, remember them
        title, composer = song.credits()
    transcribe(song, device, progress=progress)
    beats(song, device, progress=progress)
    build(song, title=title, composer=composer, progress=progress)
    progress("done", song.slug)
    return song
