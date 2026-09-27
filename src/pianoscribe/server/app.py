"""Local practice server: the song library, each song's viewer files and audio, adding songs,
correcting scores, and the web page.

Run with `pianoscribe serve` (add `--host 0.0.0.0` to open it from an iPad on the same Wi-Fi).
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import pipeline
from ..library import LIBRARY, Song
from ..notation import edits as E
from .jobs import JobRunner

WEB = Path(__file__).resolve().parents[3] / "web"
SAFE = re.compile(r"^[\w][\w.-]*$")  # song slugs and file names: no slashes, no leading dots
UPLOADS = Path(tempfile.gettempdir()) / "pianoscribe-uploads"

app = FastAPI(title="pianoscribe")
jobs = JobRunner()


def _song(slug: str) -> Song:
    if not SAFE.match(slug) or not (LIBRARY / slug).is_dir():
        raise HTTPException(404, f"No song {slug!r}")
    return Song(slug, LIBRARY / slug)


def _file(path: Path | None) -> FileResponse:
    if path is None or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)  # supports Range requests


# ---- library -------------------------------------------------------------------------------

@app.get("/api/songs")
def songs() -> list[dict]:
    """Songs that have a rendered viewer."""
    out = []
    for d in sorted(p for p in LIBRARY.iterdir() if p.is_dir() and SAFE.match(p.name)):
        if (d / "score" / "view" / "sync.json").is_file():
            song = Song(d.name, d)
            title, composer = song.credits()
            out.append({"slug": d.name, "title": title or d.name, "composer": composer,
                        "edits": len(E.load(d / "score" / "edits.json"))})
    return out


@app.get("/api/songs/{slug}/view/{name}")
def view_file(slug: str, name: str) -> FileResponse:
    if not SAFE.match(name):
        raise HTTPException(404, "Not found")
    return _file(_song(slug).view_dir / name)


# Explicit types: macOS's MIME table calls .m4a "audio/mp4a-latm", which Chrome won't play.
AUDIO_TYPES = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".webm": "audio/webm", ".opus": "audio/ogg",
               ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav"}


@app.get("/api/songs/{slug}/audio")
def audio(slug: str) -> FileResponse:
    path = _song(slug).playback_audio
    if path is None or not path.is_file():
        raise HTTPException(404, "No audio")
    return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix, "application/octet-stream"))


@app.get("/api/songs/{slug}/pdf")
def pdf(slug: str) -> FileResponse:
    return _file(_song(slug).pdf)


# ---- adding songs --------------------------------------------------------------------------

@app.get("/api/search")
def search(q: str) -> list[dict]:
    from ..sources.youtube import search as yt_search

    if not q.strip():
        return []
    return yt_search(q.strip())


@app.get("/api/probe")
def probe(url: str) -> dict:
    from ..sources.youtube import probe as yt_probe

    return yt_probe(url)


class AddRequest(BaseModel):
    source: str  # a URL
    title: str | None = None
    composer: str | None = None
    slug: str | None = None


def _start_add(source: str, title: str | None, composer: str | None, slug: str | None, label: str) -> dict:
    if slug and not SAFE.match(slug):
        raise HTTPException(400, "Invalid slug")

    def run(progress) -> str:
        return pipeline.add_song(source, slug or None, title or None, composer or None, progress=progress).slug

    return jobs.submit(label, run).public()


@app.post("/api/add")
def add(req: AddRequest) -> dict:
    if not re.match(r"^https?://", req.source):
        raise HTTPException(400, "Expected a URL; upload local files instead")
    return _start_add(req.source, req.title, req.composer, req.slug, req.title or req.source)


@app.post("/api/upload")
def upload(file: UploadFile = File(...), title: str = Form(""), composer: str = Form(""), slug: str = Form("")) -> dict:
    UPLOADS.mkdir(exist_ok=True)
    name = Path(file.filename or "upload").name
    dest = UPLOADS / (pipeline.slugify(Path(name).stem) + Path(name).suffix.lower())
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return _start_add(str(dest), title, composer, slug or pipeline.slugify(title or Path(name).stem), title or name)


@app.get("/api/jobs")
def job_list() -> list[dict]:
    return [j.public() for j in sorted(jobs.jobs.values(), key=lambda j: -j.created)]


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    if job_id not in jobs.jobs:
        raise HTTPException(404, "No such job")
    return jobs.jobs[job_id].public()


# ---- correcting a score --------------------------------------------------------------------

class EditsRequest(BaseModel):
    pending: list[dict]
    line: bool = False


@app.get("/api/songs/{slug}/edits")
def saved_edits(slug: str) -> dict:
    return {"saved": E.load(_song(slug).edits)}


@app.post("/api/songs/{slug}/preview")
def preview(slug: str, req: EditsRequest) -> dict:
    """The score with saved + pending edits, rendered for the viewer (nothing written)."""
    return pipeline.preview(_song(slug), req.pending, line=req.line)


@app.post("/api/songs/{slug}/edits")
def save_edits(slug: str, req: EditsRequest) -> dict:
    """Append pending edits to the saved ones and rebuild everything (score, PDF, viewer)."""
    song = _song(slug)
    E.save(song.edits, E.load(song.edits) + req.pending)
    built = pipeline.build(song)
    return {"saved": len(E.load(song.edits)), "skipped": len(built.skipped_edits), "seconds": round(built.seconds, 1)}


@app.delete("/api/songs/{slug}/edits")
def revert_edits(slug: str) -> dict:
    """Drop every saved edit and rebuild the score as transcribed."""
    song = _song(slug)
    E.save(song.edits, [])
    built = pipeline.build(song)
    return {"saved": 0, "seconds": round(built.seconds, 1)}


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
