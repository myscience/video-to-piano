from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

ANALYSIS_SR = 44_100


def extract_wav(src: Path, dst: Path, sr: int = ANALYSIS_SR) -> Path:
    """Decode any audio/video file ffmpeg understands into mono PCM WAV."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
         "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )
    return dst


def load_audio(path: Path, sr: int) -> np.ndarray:
    """Mono float32 at `sr`."""
    audio, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if file_sr != sr:
        audio = soxr.resample(audio, file_sr, sr)
    return audio
