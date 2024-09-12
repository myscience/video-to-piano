import cv2
import numpy as np
from itertools import combinations
from collections import defaultdict

from tqdm.auto import trange
from dataclasses import dataclass
from PIL import Image, ImageEnhance
from typing import List, Tuple, Dict

from .utils import Configs, Color, Box, Layout
from .utils import BLACK, WHITE
from .music import RawChord

@dataclass
class Frame:
    image   : np.ndarray
    elapsed : float
    
    enhance : int = 30
    palette : Tuple[Color] = (
        (0, 0, 0),       # Black
        (255, 255, 255), # White
        (0, 0, 255),     # Blue
        (0, 255, 0),     # Green
    )
    
    @property
    def shape(self) -> Tuple[int, int]:
        return self.image.shape
    
    @property
    def quantized(self) -> np.ndarray:
        # Unroll the palette into a flat list as this is what PIL expects
        palette = [v for rgb in self.palette for v in rgb]
        
        # Zero-pad the palette to 256 RGB colors,
        # i.e. 768 values and apply to image
        palette += (768 - len(palette)) * [0]
        
        # Make tiny palette Image, one black pixel
        img_palette = Image.new('P', (1, 1))
        img_palette.putpalette(palette)
        
        # Enhance color saturation to avoid artifact of quantization
        img = Image.fromarray(self.image)
        img = ImageEnhance.Color(img).enhance(self.enhance)
        img = img.quantize(
            palette=img_palette,
            dither=Image.Dither.NONE,
        ).convert('RGB')
        
        return np.asarray(img)

def find_objs(
    frame : Frame,
    obj_col : Dict[str, Color],
    hue_span : int = 10,
    min_area : int = 750,
) -> Dict[str, List[Box]]:
    if isinstance(obj_col, Color): obj_col = [obj_col]
    
    objs = defaultdict(list)
    for key, col in obj_col.items():
        # Create a mask to extract the target color from the frame
        hue_start = col.hue - hue_span
        hue_stop  = col.hue + hue_span
        hsv = cv2.cvtColor(frame.quantized, cv2.COLOR_RGB2HSV)
        mask = cv2.inRange(hsv, (hue_start, 50, 50), (hue_stop, 255, 255))
    
        # Get the contours of the objects in the mask
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter out the contours that are too small
        h, w, *_ = frame.shape
        box = sorted([
            Box(*cv2.boundingRect(contour)) / (w, h)
            for contour in contours
            if cv2.contourArea(contour) > min_area
        ])
        
        # Only fill the dictionary if there are
        # objects detected => we can check for
        # detection by doing bool(objs)
        if box: objs[key] = box
    
    return objs

def extract_notes(
    video_path : str,
    key_layout : Layout,
    note_color : Dict[str, Color],
    skip_intro : int | None = None,
    skip_outro : int | None = None,
    early_stop : int | None = None,
    trim_areas : Tuple[slice, slice] = (slice(-250, None), slice(None, None)),
    configs : Configs = Configs(),
    verbose : bool = True,
) -> Tuple[
    Dict[str, List[RawChord]],
    Dict[str, int],
    Dict[str, List[Frame]],
]:
    '''Divide the video frames into chunks, where the split
    is decided by the color difference between the current frame
    and the target color.

    Args:
        video_path (str): The path to the video file.
        targ_color (Color): The target color used to split the video frames into chunks.
        divide_thr (float, optional): The threshold value for color difference. Defaults to 1e-3.
        skip_intro (int, optional): Number of intro frames to skip. Defaults to None.

    Returns:
        List[Frame]: A list of frames representing the divided chunks of the video.
    '''
    
    palette = [
        WHITE, BLACK, *list(note_color.values())
    ]
    
    # Load the video & check correct opening
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise ValueError(f'Could not open video file: {video_path}')
    
    # Get all the available metadata from the video
    fps = capture.get(cv2.CAP_PROP_FPS)
    frame_count  = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_width  = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    skip_intro = skip_intro or 0
    skip_outro = skip_outro or 0
    early_stop = early_stop or (frame_count - skip_outro)
    
    # Skip the intro frames if necessary
    while skip_intro:
        ret, frame = capture.read()
        skip_intro -= 1
    
    # Skip till first note is detected
    while not (old_objs := find_objs(
                last := Frame(
                    cv2.cvtColor(frame[trim_areas], cv2.COLOR_BGR2RGB),
                    capture.get(cv2.CAP_PROP_POS_MSEC),
                    palette=palette,
                ),
                note_color,    
            )):
        ret, frame = capture.read()
    
    # * Main loop to divide the video into chunks
    chords : Dict[str, List[RawChord]] = defaultdict(list)
    frames : Dict[str, List[Frame]]    = defaultdict(list)
    _frame : Dict[str, Frame] = {k : last for k in old_objs}
    for k, v in old_objs.items():
        chords[k].append(RawChord(
            key_layout[v],
            configs,
        ))
        frames[k].append(last)
    
    num_frames = 0
    feedback = trange(0, early_stop, desc='Parsing Video') if verbose else None
    while ret and num_frames < early_stop:
        ret, frame = capture.read()
        if not ret: break
        
        frame = Frame(
            cv2.cvtColor(frame[trim_areas], cv2.COLOR_BGR2RGB),
            capture.get(cv2.CAP_PROP_POS_MSEC),
            palette=palette,
        )
        
        new_objs = find_objs(
            frame,
            note_color,
        )
        
        for key in note_color:
            # If the number of objects of the target color
            # changes we mark this frame as important
            if old_objs[key] != new_objs[key]:
                boxes = new_objs[key]
                
                # Mark timing for previous chords as we got a new one
                if chords[key]:
                    prev, post = _frame[key], frame
                    for notes in chords[key][-1]._notes:
                        notes.time = post.elapsed - prev.elapsed
                
                # Add chords and frames to the respective lists
                chords[key].append(RawChord(
                    key_layout[boxes],
                    configs,
                    elapsed=frame.elapsed,
                ))
                
                frames[key].append(frame)
                
                # Update the last frame and objects
                _frame[key] = frame
                old_objs[key] = new_objs[key]
        
        num_frames += 1
        if feedback: feedback.update(1)
    
    info = {
        'video_fps' : fps,
        'video_frame_count' : frame_count,
        'video_frame_width' : frame_width,
        'video_frame_height' : frame_height,
        'video_fraction' : num_frames / frame_count,
        'notes_onset'  : {k : v[ 0].elapsed for k, v in frames.items()},
        'notes_offset' : {k : v[-1].elapsed for k, v in frames.items()},
        'detected_chords' : {k : len(v) for k, v in chords.items()},
    }
    
    # If there is a difference in onset/offset times, we
    # need to align them by inserting rests when appropriate
    keys = chords.keys()
    for key1, key2 in combinations(keys, 2):
        if abs(diff := info['notes_onset'][key1] - info['notes_onset'][key2]) > 2 * info['video_fps']:
            if diff < 0: chords[key2].insert(0, RawChord('R', time=abs(diff), info=configs))
            else:        chords[key1].insert(0, RawChord('R', time=abs(diff), info=configs))
        if abs(diff := info['notes_offset'][key1] - info['notes_offset'][key2]) > 2 * info['video_fps']:
            if diff < 0: chords[key2].append(RawChord('R', time=abs(diff), info=configs))
            else:        chords[key1].append(RawChord('R', time=abs(diff), info=configs))
    
    return chords, info, frames
