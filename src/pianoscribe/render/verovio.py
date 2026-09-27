"""MusicXML -> SVG pages + a note timemap for the practice viewer (rendered once, server side)."""

from __future__ import annotations

import json
from pathlib import Path

import verovio


def render_view(xml: Path, out: Path, page_width: int = 2100, scale: int = 40) -> int:
    """Writes page-<N>.svg, line.svg and notes.json into `out`; returns the page count.

    line.svg: the whole piece as one endless system, for the viewer's scrolling-line mode. It is
    a re-layout of the same loaded document, so note ids match the pages and notes.json.
    notes.json: {id: [q_on, q_off]} in quarter notes from the start of bar 1 (Verovio's qstamp),
    for every note, so the viewer can highlight what sounds at any position and seek to a
    clicked note.
    """
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("page-*.svg"):
        old.unlink()
    tk = verovio.toolkit()
    tk.setOptions({"pageWidth": page_width, "pageHeight": round(page_width * 1.414), "scale": scale,
                   "adjustPageHeight": True, "footer": "none"})
    if not tk.loadFile(str(xml)):
        raise RuntimeError(f"Verovio could not read {xml}")
    pages = tk.getPageCount()
    for page in range(1, pages + 1):
        (out / f"page-{page}.svg").write_text(tk.renderToSVG(page))
    spans: dict[str, list[float]] = {}
    for event in tk.renderToTimemap({"includeRests": False}):
        for note_id in event.get("on", []):
            spans[note_id] = [event["qstamp"], event["qstamp"]]
        for note_id in event.get("off", []):
            if note_id in spans:
                spans[note_id][1] = event["qstamp"]
    (out / "notes.json").write_text(json.dumps(spans))
    tk.setOptions({"breaks": "none", "adjustPageWidth": True})
    tk.redoLayout()
    (out / "line.svg").write_text(tk.renderToSVG(1))
    return pages
