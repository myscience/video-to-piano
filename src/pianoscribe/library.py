"""Per-song folders. Every pipeline stage reads/writes files here, so any stage
can be re-run in isolation and every intermediate can be inspected."""

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

    @property
    def audio(self) -> Path:
        """Canonical analysis audio: mono 44.1 kHz WAV."""
        return self.root / "audio.wav"

    @property
    def beats(self) -> Path:
        return self.root / "beats.json"

    @property
    def activations(self) -> Path:
        """Cached per-frame beat/downbeat activations."""
        return self.root / "activations.npz"

    @property
    def truth_bars(self) -> Path:
        """Downbeats read from a Synthesia video (answer key for beat tracking)."""
        return self.root / "truth_bars.json"

    def notes(self, backend: str) -> Path:
        return self.root / "notes" / f"{backend}.mid"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def read_meta(self) -> dict[str, Any]:
        return json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}

    def update_meta(self, **fields: Any) -> None:
        self.meta_path.write_text(json.dumps(self.read_meta() | fields, indent=2))
