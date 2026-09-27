"""Local practice server: the song library, each song's viewer files and audio, and the web page.

Run with `pianoscribe serve` (add `--host 0.0.0.0` to open it from an iPad on the same Wi-Fi).
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..library import LIBRARY, Song

WEB = Path(__file__).resolve().parents[3] / "web"
SAFE = re.compile(r"^[\w][\w.-]*$")  # song slugs and file names: no slashes, no leading dots

app = FastAPI(title="pianoscribe")


def _song(slug: str) -> Song:
    if not SAFE.match(slug) or not (LIBRARY / slug).is_dir():
        raise HTTPException(404, f"No song {slug!r}")
    return Song(slug, LIBRARY / slug)


def _file(path: Path | None) -> FileResponse:
    if path is None or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)  # supports Range requests, so the audio can seek


@app.get("/api/songs")
def songs() -> list[dict]:
    """Songs that have a rendered viewer."""
    out = []
    for d in sorted(p for p in LIBRARY.iterdir() if p.is_dir() and SAFE.match(p.name)):
        if (d / "score" / "view" / "sync.json").is_file():
            title, composer = Song(d.name, d).credits()
            out.append({"slug": d.name, "title": title or d.name, "composer": composer})
    return out


@app.get("/api/songs/{slug}/view/{name}")
def view_file(slug: str, name: str) -> FileResponse:
    if not SAFE.match(name):
        raise HTTPException(404, "Not found")
    return _file(_song(slug).view_dir / name)


# Explicit types: macOS's MIME table calls .m4a "audio/mp4a-latm", which Chrome won't play.
AUDIO_TYPES = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".webm": "audio/webm", ".opus": "audio/ogg",
               ".mp3": "audio/mpeg", ".wav": "audio/wav"}


@app.get("/api/songs/{slug}/audio")
def audio(slug: str) -> FileResponse:
    path = _song(slug).playback_audio
    if path is None or not path.is_file():
        raise HTTPException(404, "No audio")
    return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix, "application/octet-stream"))


@app.get("/api/songs/{slug}/pdf")
def pdf(slug: str) -> FileResponse:
    return _file(_song(slug).pdf)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
