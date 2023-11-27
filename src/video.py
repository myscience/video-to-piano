import cv2
import numpy as np

from tqdm.auto import trange

from .misc import mse
from .misc import default
from .image import trim_frame
from .image import is_keyboard
from .image import quantize_palette

from .image import COLOR

from typing import Tuple, List, Dict, Any

default_palette = [COLOR.WHITE, COLOR.BLACK, COLOR.RED, COLOR.GREEN]

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
) -> Tuple[np.ndarray | None, int]:
    new_frame = old_frame.copy()
    skipped = 0
    while mse(new_frame, old_frame) < thr:
        ret, new_frame = capture.read()  

        if new_frame is None: break
        
        new_frame = trim_frame(new_frame)

        skipped += 1

    return new_frame, skipped

def get_frames(
    video_path : str,
    max_frames : int | float | None = None,
    palette : List[COLOR] | None = None,
    color_enhance : int = 10,
    frame_diff_thr : float = 5e-4,
    black_flags : List[int] | None = None,
    verbose : bool = False,
) -> Tuple[List[np.ndarray], List[float], Dict[str, Any]]:
    
    capture = cv2.VideoCapture(video_path)
    MAX_FRAMES = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

    if isinstance(max_frames, int): max_frames = min(max_frames, MAX_FRAMES)
    if isinstance(max_frames, float): max_frames = min(int(MAX_FRAMES * max_frames), MAX_FRAMES)

    palette = default(palette, default_palette)
    max_frames = default(max_frames, MAX_FRAMES)
    black_flags = default(black_flags, [])

    fps = capture.get(cv2.CAP_PROP_FPS)

    frame, intro_skipped = skip_intro(capture)

    frame_seen = intro_skipped
    frame_count = 0 
    frames, times, stamps = [], [], []

    feedback = trange(intro_skipped, max_frames, desc='Extracting Frames') if verbose else None

    while frame_seen < max_frames:
        frame, skipped = go_to_next(capture, frame, thr=frame_diff_thr)

        # Exit clause in case of no next frame available 
        # (i.e. the video has an outro)
        if frame is None:
            if feedback: feedback.update(max_frames - frame_seen)
            break

        # Quantize the frame using provided color palette
        quantized = quantize_palette(frame, palette=palette, color_enhance=color_enhance)

        times.append(skipped)
        frames.append(quantized)

        frame_seen  += skipped
        frame_count += 1

        # Get timestamp for each frame
        stamps.append(capture.get(cv2.CAP_PROP_POS_MSEC))

        if feedback: feedback.update(skipped)

    # NOTE: Because our functions measures the SKIPPED number of frames, to actually
    #       measure the duration of each frame we need to referred to the next frame
    #       skipped frames to know how long the previous frame lasted, with the last
    #       detected frame having an unknown duration because we haven't detected its
    #       end yet! Moreover, we discard how many skipped frame there were for the
    #       intro as that's of no use to us. We mark unknown duration with [0].
    times  = times[1:] + [0]
    stamps = np.diff(stamps).tolist()

    # If some frames are black-flagged, we remove it
    for flag in black_flags:
        del times[flag]
        del frames[flag]
        del stamps[flag]

    info = {
        'fps' : fps,
        'frame_seen'  : frame_seen,
        'frame_count' : frame_count,
        'intro_skipped' : intro_skipped,
        'timestamps' : stamps,
    }

    return frames, times, info