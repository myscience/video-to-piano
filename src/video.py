import cv2
import numpy as np

from .misc import mse
from .image import trim_frame
from .image import is_keyboard
from .image import quantize_palette

from typing import Tuple, List

def skip_intro(
    capture : cv2.VideoCapture
) -> Tuple[np.ndarray, int]:
    # Skip the video introduction
    skipped = 0
    ret, frame = capture.read()
    frame = trim_frame(frame)

    while not is_keyboard(frame):
        ret, frame = capture.read()
        frame = trim_frame(frame)

        skipped += 1

    return frame, skipped

def go_to_next(
    capture : cv2.VideoCapture,
    old_frame : np.ndarray,
    thr : float = 1e-3,
) -> Tuple[np.ndarray, int]:
    new_frame = old_frame.copy()
    skipped = 0
    while mse(new_frame, old_frame) < thr:
        ret, new_frame = capture.read()  
        new_frame = trim_frame(new_frame)

        skipped += 1

    return new_frame, skipped