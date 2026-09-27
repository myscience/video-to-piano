"""MusicXML -> PDF (and PNG previews) through LilyPond's own converter."""

from __future__ import annotations

import subprocess
from pathlib import Path


def musicxml_to_pdf(xml: Path, pdf: Path, png: bool = False) -> Path:
    """Engrave `xml` into `pdf`. LilyPond's .ly (and <stem>-page<N>.png if `png`) stay next to `xml`."""
    xml, pdf = xml.resolve(), pdf.resolve()
    ly = xml.with_suffix(".ly")
    ly.unlink(missing_ok=True)  # musicxml2ly would keep the old one as a '.ly~' backup
    for old in xml.parent.glob(f"{xml.stem}-page*.png"):
        old.unlink()
    subprocess.run(["musicxml2ly", "--no-beaming", "-o", str(ly), str(xml)], check=True, capture_output=True)
    fmt = ["--png", "-dresolution=110"] if png else []
    subprocess.run(["lilypond", "--pdf", *fmt, "-o", str(xml.with_suffix("")), str(ly)],
                   check=True, capture_output=True, cwd=xml.parent)
    xml.with_suffix(".pdf").replace(pdf)
    return pdf
