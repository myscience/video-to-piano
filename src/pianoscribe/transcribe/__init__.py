from __future__ import annotations

from .base import NoteEvent, Transcriber, Transcription

BACKENDS = ("transkun", "bytedance")


def get_transcriber(name: str, device: str = "auto") -> Transcriber:
    # Imported lazily: each backend pulls in heavy, optional dependencies.
    if name == "transkun":
        from .transkun_backend import TranskunTranscriber
        return TranskunTranscriber(device)
    if name == "bytedance":
        from .bytedance_backend import ByteDanceTranscriber
        return ByteDanceTranscriber(device)
    raise ValueError(f"Unknown backend {name!r}; choose from {BACKENDS}")


__all__ = ["BACKENDS", "NoteEvent", "Transcriber", "Transcription", "get_transcriber"]
