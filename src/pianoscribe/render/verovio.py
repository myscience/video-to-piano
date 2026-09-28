"""MusicXML -> SVG pages + a note timemap for the practice viewer (rendered on the server)."""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

import verovio

OUR_ID = re.compile(r"^n(\d+)t-?\d+v\d+$")  # "n<pitch>t<tick>v<voice>" (notation/musicxml.py)


def render(xml: str, line: bool = True, page_width: int = 2100, scale: int = 40) -> dict:
    """{"pages": [svg, ...], "line": svg or None, "notes": {id: [q_on, q_off, midi_pitch]}}.

    q is in quarter notes from the start of bar 1 (Verovio's qstamp): the viewer highlights what
    sounds at any position, seeks to a clicked note, and finds the note an edit targets.
    line: also lay the piece out as one endless system (the viewer's scrolling-line mode). It's a
    re-layout of the same loaded document, so its note ids match the pages and `notes`.
    """
    tk = verovio.toolkit(False)  # fonts load below, from the right place
    # The wheel's compiled-in resource path is its build machine's temp folder; the package fixes
    # the *default* at import, but that doesn't reach other threads (every server request): fonts
    # failed to load there and some scores wouldn't render. Point each toolkit at the fonts.
    tk.setResourcePath(str(files("verovio") / "data"))
    tk.setOptions({"pageWidth": page_width, "pageHeight": round(page_width * 1.414), "scale": scale,
                   "adjustPageHeight": True, "footer": "none"})
    if not tk.loadData(xml):
        raise RuntimeError("Verovio could not read the MusicXML")
    pages = [tk.renderToSVG(p) for p in range(1, tk.getPageCount() + 1)]
    spans: dict[str, list] = {}
    for event in tk.renderToTimemap({"includeRests": False}):
        for note_id in event.get("on", []):
            named = OUR_ID.match(note_id)  # our writer's ids carry the pitch; others need a lookup
            pitch = int(named.group(1)) if named else tk.getMIDIValuesForElement(note_id).get("pitch")
            spans[note_id] = [event["qstamp"], event["qstamp"], pitch]
        for note_id in event.get("off", []):
            if note_id in spans:
                spans[note_id][1] = event["qstamp"]
    line_svg = None
    if line:
        tk.setOptions({"breaks": "none", "adjustPageWidth": True})
        tk.redoLayout()
        line_svg = tk.renderToSVG(1)
    return {"pages": pages, "line": line_svg, "notes": spans}


def render_view(xml: Path, out: Path) -> int:
    """Writes page-<N>.svg, line.svg and notes.json into `out`; returns the page count."""
    out.mkdir(parents=True, exist_ok=True)
    view = render(xml.read_text())
    for old in out.glob("page-*.svg"):
        old.unlink()
    for i, svg in enumerate(view["pages"], start=1):
        (out / f"page-{i}.svg").write_text(svg)
    (out / "line.svg").write_text(view["line"])
    (out / "notes.json").write_text(json.dumps(view["notes"]))
    return len(view["pages"])
