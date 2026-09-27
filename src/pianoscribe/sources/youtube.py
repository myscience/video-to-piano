from __future__ import annotations

from pathlib import Path

import yt_dlp

from ..library import Song
from .audio import extract_wav


def download_audio(url: str, song: Song) -> Path:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(song.root / "source_audio.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = Path(ydl.prepare_filename(info))

    song.update_meta(
        title=info.get("title"),
        channel=info.get("channel"),
        url=info.get("webpage_url", url),
        duration=info.get("duration"),
        source_audio=downloaded.name,
    )
    return extract_wav(downloaded, song.audio)
