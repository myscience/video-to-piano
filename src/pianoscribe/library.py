"""Per-song folders. Every pipeline stage reads/writes files here, so any stage
can be re-run in isolation and every intermediate can be inspected.

    library/<slug>/
      <slug>.pdf     the final score
      source/        meta.json, video.mp4 (optional), original.<ext>, audio.wav
      notes/         <backend>.mid: transcriptions (transkun, bytedance, ensemble, ...)
      rhythm/        activations.npz, beats.json
      truth/         notes.mid, bars.json: answer key read from a Synthesia video (evaluation)
      score/         score.musicxml, score.ly, score-page<N>.png
      eval/          roll.png
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LIBRARY = Path("library")


@dataclass(frozen=True)
class Song:
    slug: str
    root: Path

    @classmethod
    def get(cls, slug: str, library: Path = LIBRARY) -> Song:
        root = library / slug
        root.mkdir(parents=True, exist_ok=True)
        return cls(slug, root)

    def _in(self, folder: str, name: str) -> Path:
        """root/folder/name, creating the folder on first use."""
        (self.root / folder).mkdir(exist_ok=True)
        return self.root / folder / name

    # source/
    @property
    def meta_path(self) -> Path:
        return self._in("source", "meta.json")

    @property
    def video(self) -> Path:
        """Optional Synthesia-style video (the answer key for `pianoscribe truth`)."""
        return self._in("source", "video.mp4")

    @property
    def download_template(self) -> str:
        """yt-dlp output template for the downloaded original audio."""
        return str(self._in("source", "original.%(ext)s"))

    @property
    def audio(self) -> Path:
        """Canonical analysis audio: mono 44.1 kHz WAV."""
        return self._in("source", "audio.wav")

    # notes/
    def notes(self, backend: str) -> Path:
        return self._in("notes", f"{backend}.mid")

    def all_notes(self) -> dict[str, Path]:
        """Every transcription in notes/, by backend name."""
        return {p.stem: p for p in sorted((self.root / "notes").glob("*.mid"))}

    # rhythm/
    @property
    def activations(self) -> Path:
        """Cached per-frame beat/downbeat activations."""
        return self._in("rhythm", "activations.npz")

    @property
    def beats(self) -> Path:
        return self._in("rhythm", "beats.json")

    # truth/
    @property
    def truth_notes(self) -> Path:
        """Notes (with hands) read from a Synthesia video."""
        return self._in("truth", "notes.mid")

    @property
    def truth_bars(self) -> Path:
        """Bar lines (downbeats) read from a Synthesia video."""
        return self._in("truth", "bars.json")

    # score/ and the final PDF
    @property
    def score(self) -> Path:
        """The score as MusicXML; LilyPond's .ly and page PNGs go next to it."""
        return self._in("score", "score.musicxml")

    @property
    def pdf(self) -> Path:
        return self.root / f"{self.slug}.pdf"

    # eval/
    def eval_file(self, name: str) -> Path:
        return self._in("eval", name)

    # metadata
    def read_meta(self) -> dict[str, Any]:
        return json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}

    def update_meta(self, **fields: Any) -> None:
        self.meta_path.write_text(json.dumps(self.read_meta() | fields, indent=2))

    def credits(self) -> tuple[str, str]:
        """(title, composer) guessed from a video title like 'exile - Taylor Swift | Piano Tutorial'."""
        raw = self.read_meta().get("title") or self.slug
        head = raw.split("|")[0].strip()
        title, _, composer = head.partition(" - ")
        return title.strip(), composer.strip()
