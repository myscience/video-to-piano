"""Transkun (Yan & Duan): transformer + neural semi-CRF, weights ship with the package."""

from __future__ import annotations

from importlib.resources import files

import numpy as np
import torch

from .base import SUSTAIN_CC, NoteEvent, Transcription, pick_device


class TranskunTranscriber:
    name = "transkun"
    sample_rate = 44_100

    def __init__(self, device: str = "auto") -> None:
        import moduleconf

        self.device = pick_device(device)
        pretrained = files("transkun") / "pretrained"
        conf_manager = moduleconf.parseFromFile(str(pretrained / "2.0.conf"))
        model_cls = conf_manager["Model"].module.TransKun
        self.model = model_cls(conf=conf_manager["Model"].config)

        ckpt = torch.load(str(pretrained / "2.0.pt"), map_location="cpu", weights_only=False)
        state = ckpt.get("best_state_dict", ckpt.get("state_dict"))
        self.model.load_state_dict(state, strict=False)
        self.model.to(self.device).eval()
        assert self.model.fs == self.sample_rate

    @torch.inference_mode()
    def transcribe(self, audio: np.ndarray) -> Transcription:
        x = torch.from_numpy(audio.astype(np.float32)).reshape(-1, 1).to(self.device)
        raw = self.model.transcribe(x, discardSecondHalf=False)

        notes, pedal = [], []
        for ev in raw:
            if ev.pitch > 0:
                notes.append(NoteEvent(int(ev.pitch), float(ev.start), float(ev.end), int(ev.velocity)))
            elif -ev.pitch == SUSTAIN_CC:  # control events are encoded as negative pitch
                pedal.append((float(ev.start), float(ev.end)))
        return Transcription(notes, pedal)
