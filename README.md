# pianoscribe 🎹

Turn solo-piano recordings into sheet music you can practice from.

**Status:** end to end. Audio → notes (~96% note F1 on a real tutorial, measured against the
video's own notes) → beats, bars, hands and voices → an engraved PDF, plus a practice viewer that
lights up the notes as the original recording plays. See [DESIGN.md](DESIGN.md) for the
architecture, results and roadmap.

```bash
uv sync --extra transkun --extra bytedance     # + model checkpoints, see DESIGN.md
uv run pianoscribe fetch "<youtube url>" --song my-song
uv run pianoscribe transcribe my-song --backend all && uv run pianoscribe merge my-song
uv run pianoscribe beats my-song
uv run pianoscribe score my-song               # -> library/my-song/my-song.pdf
uv run pianoscribe serve                       # -> http://localhost:8765
```
