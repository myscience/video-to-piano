"""ByteDance high-resolution piano transcription (Kong et al.): CRNN with onset/offset regression."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import numpy as np
import torch

from .base import NoteEvent, Transcription, pick_device

DEFAULT_CHECKPOINT = Path("models/bytedance/note_F1=0.9677_pedal_F1=0.9186.pth")


class ByteDanceTranscriber:
    name = "bytedance"
    sample_rate = 16_000

    def __init__(self, device: str = "auto", checkpoint: Path = DEFAULT_CHECKPOINT) -> None:
        from piano_transcription_inference import PianoTranscription

        if not checkpoint.exists():
            raise FileNotFoundError(
                f"Missing checkpoint {checkpoint}; download it from "
                "https://zenodo.org/record/4034264 (see DESIGN.md)."
            )
        self.device = pick_device(device)
        # The library only moves the model for 'cuda'; build on CPU and move it
        # ourselves so MPS works too. Its forward() follows the model's device.
        with contextlib.redirect_stdout(io.StringIO()):
            self._pt = PianoTranscription(checkpoint_path=str(checkpoint), device=torch.device("cpu"))
        self._pt.model.to(self.device)

    @torch.inference_mode()
    def transcribe(self, audio: np.ndarray) -> Transcription:
        with contextlib.redirect_stdout(io.StringIO()):  # it prints every segment
            out = self._pt.transcribe(audio.astype(np.float32), midi_path=None)

        notes = [
            NoteEvent(int(e["midi_note"]), float(e["onset_time"]), float(e["offset_time"]), int(e["velocity"]))
            for e in out["est_note_events"]
        ]
        pedal = [(float(e["onset_time"]), float(e["offset_time"])) for e in out["est_pedal_events"]]
        return Transcription(notes, pedal)
