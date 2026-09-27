from __future__ import annotations

from pathlib import Path

import yt_dlp

from ..library import Song
from .audio import extract_wav


def download_audio(url: str, song: Song) -> Path:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": song.download_template,
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
        original_audio=downloaded.name,
    )
    return extract_wav(downloaded, song.audio)


def probe(url: str) -> dict:
    """Title, channel and duration of a URL, without downloading it."""
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return {"title": info.get("title"), "channel": info.get("channel"), "duration": info.get("duration"),
            "url": info.get("webpage_url", url)}


def search(query: str, n: int = 8) -> list[dict]:
    """YouTube search results (metadata only, fast): the viewer's 'add a song' dialog."""
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    out = []
    for e in info.get("entries", []):
        vid = e.get("id")
        if not vid:
            continue
        thumbs = e.get("thumbnails") or []
        out.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": e.get("title"),
                    "channel": e.get("channel") or e.get("uploader"), "duration": e.get("duration"),
                    "thumbnail": thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"})
    return out
