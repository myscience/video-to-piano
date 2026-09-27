# pianoscribe 🎹

Turn solo-piano recordings into sheet music you can practice from.

**Status:** audio → notes works (~96% note F1 on a real tutorial, measured against the video's
own notes). Score generation (rhythm, hands, MusicXML/PDF) and the practice viewer are next.
See [DESIGN.md](DESIGN.md) for the architecture, results and roadmap.

```bash
uv sync --extra transkun --extra bytedance
uv run pianoscribe fetch "<youtube url>" --song my-song
uv run pianoscribe transcribe my-song          # -> library/my-song/notes/transkun.mid
```
