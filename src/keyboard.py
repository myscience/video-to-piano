import numpy as np
from itertools import cycle

from .misc import find_closest_idx
from .image import Bbox, COLOR

from collections import defaultdict

from typing import List, Tuple, Dict

white_notes = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
black_sharp = ['C#', 'D#', None, 'F#', 'G#', 'A#', None]
black_flat  = [None, 'Db', 'Eb', None, 'Gb', 'Ab', 'Bb']

def is_black_note(bbox_height : float) -> bool:
    return bbox_height < 0.8

def get_keyboard_layout(
    start_note : str = 'C',
    end_note : str = 'B',
    num_octaves : int = 7,
    start_octave : int = 1,
    # separate_bw : bool = False,
    black_key_notation : str = 'sharp',
    trim_key_layout : bool = False,
    clean_fake_keys : bool = True,
) -> List[str] | Tuple[List[str], List[str]]:
    '''
        Generate the keyboard layout based on the start and end note.
    '''

    match black_key_notation:
        case 'sharp': black_notes = black_sharp
        case 'flat' : black_notes = black_flat
        case _: raise ValueError(f'Unknown black key notation: {black_key_notation}')

    octaves = list(map(str, np.repeat(range(num_octaves), len(white_notes)) + start_octave)) + [str(num_octaves + start_octave)]
    white_kb = ['-'.join((str(note), octave)) for note, octave in zip(cycle(white_notes), octaves)]
    black_kb = ['-'.join((str(note), octave)) for note, octave in zip(cycle(black_notes), octaves)]
    white_kb = white_kb[white_notes.index(start_note):-(len(white_notes) - white_notes.index(end_note))]
    black_kb = black_kb[white_notes.index(start_note):-(len(black_notes) - white_notes.index(end_note))]

    if trim_key_layout:
        white_kb, black_kb = trim_borders(
            white_kb, black_kb, start_note, end_note,
            notation=black_key_notation,
            clean_fake=clean_fake_keys,
        )

    # if separate_bw:
    #     keyboard = (white_kb, black_kb)
    # else:
    #     keyboard = [(w, b) if black_key_notation == 'sharp' else (b, w) for w, b in zip(white_kb, black_kb)]
    #     keyboard = [note for pair in keyboard for note in pair]

    # if separate_bw:
    #     if trim_black_borders:
    #         black_kb = black_kb[:-1] if black_key_notation == 'sharp' else black_kb[1:]
            
    #     if clean_fake_black_keys:
    #         black_kb = [note for note in black_kb if 'None' not in note]

    #     keyboard = (white_kb, black_kb)

    # else:
    #     keyboard = [(w, b) if black_key_notation == 'sharp' else (b, w) for w, b in zip(white_kb, black_kb)]
    #     keyboard = [note for pair in keyboard for note in pair]

    #     if trim_black_borders:
    #         keyboard = keyboard[:-1] if black_key_notation == 'sharp' else keyboard[1:]
        
    #     if clean_fake_black_keys:
    #         keyboard = [note for note in keyboard if 'None' not in note]

    return white_kb, black_kb

def get_keyboard_spacings(
    num_octaves=7,
    start_note='D',
    end_note='G',
    black_key_notation : str = 'sharp',
) -> Tuple[np.ndarray, np.ndarray]:
    white_kb, black_kb = get_keyboard_layout(
        start_note=start_note,
        end_note=end_note,
        num_octaves = num_octaves,
        black_key_notation=black_key_notation,
        # separate_bw = True,
        trim_key_layout = True,
        clean_fake_keys = False,
    )

    err_msg = 'We need the raw unclean keyboard layout for the spacings, please compute layout by setting `trim_black_borders=False`, `clean_fake_black_keys=False`'
    assert len(white_kb) == len(black_kb), err_msg

    # White spacings are easy as they are all equidistance
    half_sp = .5 / len(white_kb)
    white_sp = np.linspace(0., 1. - 1 / len(white_kb), num = len(white_kb)) + half_sp

    # Special care is needed for black spacings
    mult = +1 if black_key_notation == 'sharp' else -1
    black_sp = np.array([w_sp + mult * (half_sp) for note, w_sp in zip(black_kb, white_sp) if 'None' not in note])

    return white_sp, black_sp

def find_notes(
    bboxes : List[Tuple[Bbox, COLOR]],
    key_layout : Tuple[List[str], List[str]],
    key_spacings : Tuple[np.ndarray, np.ndarray],
    color_hand_map : Dict[COLOR, str],
) -> Dict[str, List[str]]:
    '''
        This functions maps detected notes bounding boxes
        in an image into note names given the image keyboard
        layout and keyboard spacings.
    '''

    w_notes, b_notes = key_layout
    w_space, b_space = key_spacings

    notes = defaultdict(list)

    for (x, y, w, h), col in bboxes:
        # Control whether it's a black or white note from the bbox height
        if is_black_note(h): note = b_notes[find_closest_idx(b_space, x)]
        else:                note = w_notes[find_closest_idx(w_space, x)]

        # Use color-coding to identify which hand is playing this note
        hand = color_hand_map[col.value]

        notes[hand].append(note)

    return notes

def trim_borders(
    white_kb : List[str],
    black_kb : List[str],
    start_note : str,
    end_note : str,
    notation : str = 'sharp',
    clean_fake : bool = False,
) -> Tuple[List[str], List[str]]:
    '''
        Trim the white and black keyboard layouts to
        include only the notes between the start and end note.
    '''

    is_start_white = start_note in white_notes
    is_end_white = end_note in white_notes

    erase_idx = -1 if notation == 'sharp' else 0

    if is_start_white: black_kb[erase_idx] = 'None'
    else: white_kb[0] = 'None'

    if is_end_white: pass
    else: white_kb[-1] = 'None'

    if clean_fake:
        white_kb = [note for note in white_kb if 'None' not in note]
        black_kb = [note for note in black_kb if 'None' not in note]

    return white_kb, black_kb