import numpy as np
from .misc import Configs, Box
from typing import List, Tuple

from itertools import cycle

class Layout:
    def __init__(
        self,
        keys : Tuple[List[str], List[str]],
        dims : Tuple[List[float], List[float]]
    ) -> None:
        self.keys = keys
        self.dims = dims
    
    def __getitem__(self, idx : Box | List[Box]) -> str | List[str]:
        if isinstance(idx, Box): return self._lookup(idx)
        if len(idx) == 0: return ['R'] # Rest
        else: return [self._lookup(box) for box in idx]
    
    def _lookup(self, box : Box) -> str:
        # Get box position and dimension
        x, y, w, h = box.x, box.y, box.w, box.h
        
        # Get the closest index for the x and y position
        # NOTE: We distinguish between black and white keys
        white_k, black_k = self.keys
        white_p, black_p = self.dims
        if h < 0.8: return black_k[self._find_closest_idx(black_p, x + w / 2)]
        else:       return white_k[self._find_closest_idx(white_p, x + w / 2)]
        
    def _find_closest_idx(
        self,
        array : list | np.ndarray,
        target : float,
        sorted : bool = True
    ) -> int:
        if not sorted: array = np.sort(array)
        
        idx = np.searchsorted(array, target)
        idx = np.clip(idx, 1, len(array) - 1)
        l, r = array[idx-1], array[idx]
        idx -= target - l < r - target
        return idx

def get_layout(
    config : Configs,
) -> Layout:
    '''Generate the keyboard layout based on the start and end note and
    provides the array of central position for each black and white key.
    This in turn can be used to extract the keys from the Boxes obtained
    from the video frames.

    Args:
        num_octaves (int, optional): _description_. Defaults to 7.
        first_note (Note, optional): _description_. Defaults to 'D'.
        last_note (Note, optional): _description_. Defaults to 'G'.
        notation (Literal[&#39;sharp&#39;, &#39;flat&#39;], optional): _description_. Defaults to 'sharp'.
    '''
        
    white_notes = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
    black_sharp = ['C#', 'D#', None, 'F#', 'G#', 'A#', None]
    black_flat  = [None, 'Db', 'Eb', None, 'Gb', 'Ab', 'Bb']
    
    # * Get the keyboard layout, i.e. the list of notes split between white and black keys
    match config.notation:
        case 'sharp': black_notes = black_sharp
        case 'flat' : black_notes = black_flat
        case _: raise ValueError(f'Unknown black key notation: {config.notation}')

    octaves = list(map(str, np.repeat(range(config.num_octaves), len(white_notes)) + config.start_octave)) + [str(config.num_octaves + config.start_octave)]
    white_kb = ['-'.join((str(note), octave)) for note, octave in zip(cycle(white_notes), octaves)]
    black_kb = ['-'.join((str(note), octave)) for note, octave in zip(cycle(black_notes), octaves)]
    white_kb = white_kb[white_notes.index(config.first_note):-(len(white_notes) - white_notes.index(config.last_note))]
    black_kb = black_kb[white_notes.index(config.first_note):-(len(black_notes) - white_notes.index(config.last_note))]
    
    # Trim the boarder to make sure only note above the first_note and below the last_note are included
    is_start_white = config.first_note in white_notes
    is_end_white   = config.last_note  in white_notes

    erase_idx = -1 if config.notation == 'sharp' else 0
    if is_start_white: black_kb[erase_idx] = 'None'
    else: white_kb[0] = 'None'

    if is_end_white: pass
    else: white_kb[-1] = 'None'
    
    # * Get the central position of each key (normalized in [0, 1])
    # White spacings are easy as they are all equidistance
    half_sp = .5 / len(white_kb)
    white_sp = np.linspace(0., 1. - 1 / len(white_kb), num = len(white_kb)) + half_sp

    # Special care is needed for black spacings
    mult = +1 if config.notation == 'sharp' else -1
    black_sp = np.array([w_sp + mult * (half_sp) for note, w_sp in zip(black_kb, white_sp) if 'None' not in note])
    
    # Remove the None keys from the layout
    white_kb = [note for note in white_kb if 'None' not in note]
    black_kb = [note for note in black_kb if 'None' not in note]
    
    return Layout((white_kb, black_kb), (white_sp, black_sp))