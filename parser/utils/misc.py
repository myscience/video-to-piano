import numpy as np
import colorsys as cs

from PIL import Image
from abjad import Container, Duration, Note
from typing import Literal, Tuple
from dataclasses import dataclass
from abjad.get import leaf

Notes = Literal[
    'A', 'B', 'C', 'D', 'E', 'F', 'G',
    'A#', 'Bb', 'C#', 'Db', 'D#', 'Eb', 'F#', 'Gb', 'G#', 'Ab',
    'R', # Rest
]

NOTE_ORDER = {
    'A' : ['A', 'B', 'C', 'D', 'E', 'F', 'G'],
    'B' : ['B', 'C', 'D', 'E', 'F', 'G', 'A'],
    'C' : ['C', 'D', 'E', 'F', 'G', 'A', 'B'],
    'D' : ['D', 'E', 'F', 'G', 'A', 'B', 'C'],
    'E' : ['E', 'F', 'G', 'A', 'B', 'C', 'D'],
    'F' : ['F', 'G', 'A', 'B', 'C', 'D', 'E'],
    'G' : ['G', 'A', 'B', 'C', 'D', 'E', 'F'],
}

@dataclass
class Color:
    r : int
    g : int
    b : int
    
    @property
    def hue(self) -> int:
        h, _, _ = cs.rgb_to_hsv(self.r / 255, self.g / 255, self.b / 255)
        
        return round(h * 179)
    
    def __iter__(self):
        return iter((self.r, self.g, self.b))
    
    @staticmethod
    def from_str(color : str) -> 'Color':
        match color.lower():
            case 'black' | 'k': return BLACK
            case 'white' | 'w': return WHITE
            case 'red'   | 'r': return RED
            case 'green' | 'g': return GREEN
            case 'blue'  | 'b': return BLUE
            case _: raise ValueError(f'Unknown color: {color}')

WHITE = Color(255, 255, 255)
BLACK = Color(0, 0, 0)
RED   = Color(255, 0, 0)
GREEN = Color(0, 255, 0)
BLUE  = Color(0, 0, 255)

@dataclass
class Configs:
    BPM : int = 60 # Beats per minute
    BPM_UNIT : int = 4 # Unit of note duration for BPM - 4 for quarter note
    MIN_UNIT : int = 4 # Minimum unit of note duration - 4 for quarter note
    SEC_IN_MIN : int = 60  # Seconds in a minute
    MS_IN_SEC  : int = 1e3 # Milliseconds in a second
    
    time_signature : Tuple[int, int] = (4, 4)
    central_octave : int = 4
    num_octaves    : int = 7
    start_octave   : int = 1
    first_note : str = 'D',
    last_note  : str = 'G',
    notation   : Literal['sharp', 'flat'] = 'flat',

@dataclass
class Box:
    x : int | float
    y : int | float
    w : int | float
    h : int | float
    pos_thr : float = 40
    dim_thr : float = 40
    
    def __eq__(self, box : 'Box') -> bool:
        if not isinstance(box, Box): return False
        return  abs(self.x - box.x) < self.pos_thr and\
                abs(self.y - box.y) < self.pos_thr and\
                abs(self.w - box.w) < self.dim_thr and\
                abs(self.h - box.h) < self.dim_thr
    
    def __lt__(self, box : 'Box') -> bool:
        if not isinstance(box, Box): return False
        if self.x < box.x: return True
        if self.x == box.x and self.y < box.y: return True
        if self.x == box.x and self.y == box.y and self.w < box.w: return True
        if self.x == box.x and self.y == box.y and self.w == box.w and self.h < box.h: return True
        return False
    
    def __truediv__(self, other: Tuple[float, float]) -> 'Box':
        w, h = other
        return Box(self.x / w, self.y / h, self.w / w, self.h / h, self.pos_thr / max(h, w), self.dim_thr / max(h, w))


def get_leaf(
    voice : Container,
    which : Literal['prev', 'curr', 'next'] = 'curr'
) -> Note:
    match which:
        case 'prev': return leaf(voice, n=-1)
        case 'curr': return leaf(voice, n=+0)
        case 'next': return leaf(voice, n=+1)

def to_ms(duration : Duration, info : Configs) -> float:
    n, d = duration.pair
    return (n / d) * (info.MIN_UNIT / (info.BPM * info.BPM_UNIT)) * info.SEC_IN_MIN * info.MS_IN_SEC

def frame_to_pil(frame : np.ndarray) -> Image.Image:
    return Image.fromarray(frame)