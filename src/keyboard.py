import numpy as np
from itertools import cycle

from typing import List, Tuple

def get_keyboard_layout(
    start_note : str = 'C',
    end_note : str = 'B',
    num_octaves : int = 7,
    start_octave : int = 1,
    separate_bw : bool = False,
    black_key_notation : str = 'sharp',
    trim_black_borders : bool = False,
) -> List[str] | Tuple[List[str], List[str]]:
    '''
        Generate the keyboard layout based on the start and end note.
    '''

    white_notes = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
    black_sharp = ['C#', 'D#', None, 'F#', 'G#', 'A#', None]
    black_bemol = [None, 'Db', 'Eb', None, 'Gb', 'Ab', 'Bb']

    match black_key_notation:
        case 'sharp':   black_notes = black_sharp
        case 'bemolle': black_notes = black_bemol
        case _: raise ValueError(f'Unknown black key notation: {black_key_notation}')

    octaves = list(map(str, np.repeat(range(num_octaves), len(white_notes)) + start_octave)) + [str(num_octaves + start_octave)]
    white_kb = ['-'.join((str(note), octave)) for note, octave in zip(cycle(white_notes), octaves)]
    black_kb = ['-'.join((str(note), octave)) for note, octave in zip(cycle(black_notes), octaves)]
    white_kb = white_kb[white_notes.index(start_note):-(len(white_notes) - white_notes.index(end_note))]
    black_kb = black_kb[white_notes.index(start_note):-(len(black_notes) - white_notes.index(end_note))]

    if separate_bw:
        if trim_black_borders:
            black_kb = black_kb[:-1] if black_key_notation == 'sharp' else black_kb[1:]
            
        black_kb = [note for note in black_kb if 'None' not in note]

        keyboard = (white_kb, black_kb)

    else:
        keyboard = [(w, b) if black_key_notation == 'sharp' else (b, w) for w, b in zip(white_kb, black_kb)]
        keyboard = [note for pair in keyboard for note in pair]

        if trim_black_borders:
            keyboard = keyboard[:-1] if black_key_notation == 'sharp' else keyboard[1:]
        keyboard = [note for note in keyboard if 'None' not in note]

    return keyboard