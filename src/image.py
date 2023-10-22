from collections.abc import Iterator
import cv2
import numpy as np
from PIL import Image

from enum import Enum
from src.misc import mse

from typing import List, Tuple

Bbox = Tuple[int, int, int, int]
Color = Tuple[int|float, int|float, int|float]

class COLOR(Enum):
    RED   : Color = (255, 0, 0)
    GREEN : Color = (0, 255, 0)
    BLUE  : Color = (0, 0, 255)
    WHITE : Color = (255, 255, 255)
    BLACK : Color = (0, 0, 0)

    ALL = (RED, GREEN, BLUE, WHITE, BLACK)

    def __iter__(self) -> Iterator[int]:
        return self.value.__iter__()
    
    def __hash__(self) -> int:
        return hash(self.value)
    
    @classmethod
    def identify(cls, color : np.ndarray | Color) -> 'COLOR':
        if isinstance(color, np.ndarray):
            if color.dtype == float:
                color = (color * 255).astype(int)
        
        color = tuple(color)
        
        match color:
            case cls.RED.value:   return cls.RED
            case cls.GREEN.value: return cls.GREEN
            case cls.BLUE.value:  return cls.BLUE
            case cls.WHITE.value: return cls.WHITE
            case cls.BLACK.value: return cls.BLACK

            case _:
                raise ValueError(f'Color {color} was not identified')

def frame_as_float(frame : np.ndarray) -> np.ndarray:
    if frame.dtype == float: return frame.astype(float)

    out = frame.astype(float) / 255.

    return out.clip(0, 1)

def frame_as_int(frame : np.ndarray) -> np.ndarray:
    if frame.dtype == int: return frame.astype(np.uint8)

    frame = frame.clip(min=0, max=1)

    return (frame * 255).astype(np.uint8)

def trim_frame(
    frame : np.ndarray,
    trim : int = 250,
    black_white : bool = False,    
) -> np.ndarray:
    out = frame[-trim:, :] / 255.

    if black_white: out = out.mean(axis=-1)

    return out

def is_keyboard(frame : np.ndarray) -> bool:
    return frame.mean() < .62 and frame.mean() > .61

def frame_threshold(
    frame : np.ndarray,
    cutoff : Tuple[float, float] = (0, 1),
    cut_to : Tuple[float, float] = (0, 1),
) -> np.ndarray:
    out_frame = frame.copy()

    c_min, c_max = cutoff
    v_min, v_max = cut_to
    out_frame[out_frame.mean(axis=-1) < c_min] = v_min
    out_frame[out_frame.mean(axis=-1) > c_max] = v_max

    if len(out_frame.shape) > 2:
        out_frame = out_frame.mean(axis = -1)

    return out_frame

def quantize_palette(
    frame : np.ndarray,
    palette : List[Color],
) -> np.ndarray:
    was_float = frame.dtype == float

    # Unroll the palette into a flat list as this is what PIL expects
    palette = [v for rgb in palette for v in rgb]
    
    # Zero-pad the palette to 256 RGB colors, i.e. 768 values and apply to image
    palette += (768 - len(palette)) * [0]

    # Make tiny palette Image, one black pixel
    img_palette = Image.new('P', (1, 1))
    img_palette.putpalette(palette)
    
    img = frame_to_pil(frame).convert('RGB')
    img = img.quantize(palette=img_palette,dither=Image.Dither.NONE).convert('RGB')

    return pil_to_frame(img, as_float=was_float)

def erase_by_color(
    frame : np.ndarray,
    colors : List[Color] | np.ndarray,
    erase_to : float | Color = 0,
    tol : float = 1e-1,
) -> np.ndarray:
    
    out = frame.copy()
    for color in colors:
        out[mse(out, color, axis=-1) < tol] = erase_to

    return out

def frame_to_pil(frame : np.ndarray) -> Image.Image:    
    if frame.dtype == float: frame = frame_as_int(frame)
    
    return Image.fromarray(frame)

def pil_to_frame(img : Image.Image, as_float : bool = True) -> np.ndarray:
    out = np.asarray(img)

    return frame_as_float(out) if as_float else frame_as_int(out)

def bbox_notes(
    frame : np.ndarray,
    noise_cutoff : float = (2e-1, 1),
    cutoff_value : Tuple[float, float] = (0, 1),
    min_box_area : int = 100,
    relative_coord : bool = True,
    color_use_enum : bool = True,
) -> List[Tuple[Bbox, Color]]:
    '''
        This function identifies the bounding boxes around
        each note in the image.

        NOTE: This function assumes the frame to be color-quantized

        Returns:
        - bboxes: List of 4-tuples where each tuple is the bounding
            box around each detected note. The bbox format is (x, y, w, h)
    '''

    # Use thresholding to reduce frame noise and void false detection
    frame_thr = frame_threshold(frame, cutoff=noise_cutoff, cut_to=cutoff_value)

    frame_thr = frame_as_int(frame_thr)

    # Use OpenCV to find object contours
    contours, _ = cv2.findContours(frame_thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Filter contours by minimum spanning area
    filtered_contours = [contour for contour in contours if cv2.contourArea(contour) > min_box_area]

    # Find the bounding boxes for the notes
    bboxes = [cv2.boundingRect(contour) for contour in filtered_contours]

    # Find the dominant color of each bounding box
    # NOTE: We assume the frame as color-quantized so we can just use the
    #       color at the center of the bounding box which should be
    #       representative of the whole note
    boxcol = [frame[y + h // 2, x + w // 2] for x, y, w, h in bboxes]

    if color_use_enum: boxcol = [COLOR.identify(col) for col in boxcol]

    # If relative coordinates are requested, we rescale them based on the
    # input dimension of the original frame
    if relative_coord:
        H, W, _ = frame.shape
        bboxes = [(x / W, y / H, w / W, h / H) for x, y, w, h in bboxes]
        
    return list(zip(bboxes, boxcol))