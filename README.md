# pianoscribe 🎹

Turn solo-piano recordings into sheet music you can practice from.

**Status:** end to end. Audio → notes (~96% note F1 on a real tutorial, measured against the
video's own notes) → beats, bars, hands and voices → an engraved PDF, plus a practice viewer that
lights up the notes as the original recording plays, adds songs from YouTube or a file, and lets
you correct pitch, hand, length and timing with a live preview. See [DESIGN.md](DESIGN.md).

```bash
uv sync --extra transkun --extra bytedance     # + model checkpoints, see DESIGN.md
uv run pianoscribe serve                       # -> http://localhost:8765, then "＋ Add"
uv run pianoscribe add "<youtube url or file>" # or from the terminal -> library/<song>/<song>.pdf
```
