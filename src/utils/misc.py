import colorsys as cs

from itertools import cycle
from abjad import AssignabilityError, Chord, Container, Duration, Note, PersistentIndicatorError, Rest, StartSlur, StopSlur, attach
import numpy as np
from typing import List, Literal, Set, Tuple
from dataclasses import dataclass, field
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

WHITE = Color(255, 255, 255)
BLACK = Color(0, 0, 0)
RED   = Color(255, 0, 0)
GREEN = Color(0, 255, 0)
BLUE  = Color(0, 0, 255)

def get_leaf(
    voice : Container,
    which : Literal['prev', 'curr', 'next'] = 'curr'
) -> Note:
    match which:
        case 'prev': return leaf(voice, n=-1)
        case 'curr': return leaf(voice, n=+0)
        case 'next': return leaf(voice, n=+1)

@dataclass
class Configs:
    BPM : int = 60 # Beats per minute
    BPM_UNIT : int = 4 # Unit of note duration for BPM - 4 for quarter note
    MIN_UNIT : int = 4 # Minimum unit of note duration - 4 for quarter note
    MIN_IN_SEC : int = 60  # Seconds in a minute
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
    pos_thr : float = 10
    dim_thr : float = 10
    
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

@dataclass
class RawNote:
    name : Notes
    time : float
    info : Configs = field(default_factory=Configs)
    
    sustained : bool = False
    stop_slur  : bool = False
    start_slur : bool = False
    
    @property
    def duration(self) -> Duration:
        value = round(
            self.time / (self.info.MIN_IN_SEC / self.info.BPM /
            (self.info.MIN_UNIT / self.info.BPM_UNIT) * self.info.MS_IN_SEC)
        )
        
        return Duration(
            value,
            self.info.MIN_UNIT
        )
    
    @property
    def abjad(self) -> Note | Rest:
        if self.name == 'R': return Rest(self.duration)
        
        note = Note(
            repr(self),
            self.duration
        )
        
        if self.stop_slur:  attach(StopSlur(),  note)
        if self.start_slur: attach(StartSlur(), note)
        
        return note
    
    def __eq__(self, other : 'RawNote') -> bool:
        return self.name == other.name
    
    def __lt__(self, other : 'RawNote') -> bool:
        if self.name  == 'R': return True  # Rests are always smaller
        if other.name == 'R': return False # Rests are always smaller
        
        name_1, octave_1 = self .name.split('-')
        name_2, octave_2 = other.name.split('-')
        
        if octave_1 < octave_2: return True
        if octave_1 > octave_2: return False
        
        # Same octave, check the note name
        lookup = NOTE_ORDER[self.info.first_note]
        return lookup.index(name_1[0]) < lookup.index(name_2[0])
    
    def __add__(self, other : 'RawNote') -> 'RawNote':
        if self != other: raise ValueError(f'Cannot add different notes: {self.name} and {other.name}')
        
        return RawNote(
            self.name, 
            self.time + other.time,
            self.info,
            sustained  = self.sustained ,
            stop_slur  = self.stop_slur ,
            start_slur = self.start_slur,
        )
        
        
    
    def __radd__(self, other : int) -> 'RawNote':
        return RawNote(
            self.name,
            self.time + other,
            sustained=self.sustained,
            stop_slur=self.stop_slur,
            start_slur=self.start_slur,
        )
    
    def __str__(self) -> str:
        value, unit = self.duration.pair
        match unit:
            case 1:  sym = '♩♩♩♩'
            case 2:  sym = '♩♩'
            case 4:  sym = '♩'
            case 8:  sym = '♪'
            case 16: sym = '♬'
        if self.name == 'R': sym = '⏸️'
        try:
            _ = Note('a', self.duration)
        except AssignabilityError:
            sym = '⛔️'
        return f'{self.name} ({value}{sym}) {"🔕 " if self.sustained else ""}({self.time:.0f} ms)'
    
    def __repr__(self) -> str:
        '''We use the __repr__ method to provide the Lilypond notation
        for the note. This is useful when we want to convert the RawNote
        into a Lilypond note.

        Returns:
            str: Lilypond notation for the note.
        '''
        if self.name == 'R': return 'r'
        name, octave = self.name.split('-')
        name = name.replace('#', 's').replace('b', 'f').lower()
        pitch = "'" * (int(octave) - self.info.central_octave) + ',' * (self.info.central_octave - int(octave))
        return name + pitch
    
    def __hash__(self) -> int:
        return hash(self.name)

class RawChord:
    def __init__(
        self,
        notes : str | List[str] | RawNote | Set[RawNote],
        info : Configs = None,
        duration : float = 0,
    ) -> None:
        info = info or Configs()
        if isinstance(notes, str)    : notes = set([RawNote(notes, duration)])
        if isinstance(notes, list)   : notes = set([RawNote(note, duration) for note in notes])
        if isinstance(notes, RawNote): notes = set([notes])
        
        self._notes = notes
        self._info  = info
        
        for note in self._notes: note.info = info
    
    @property
    def notes(self) -> List[Note]:
        return [note.abjad for note in self._notes if note.duration > 0]
    
    @property
    def duration(self) -> Duration:
        return Duration(
            max([note.duration for note in self._notes])
        )
    
    # FIXME: This implementation is not working. Also, duration is just a guess
    @property
    def abjad(self) -> Chord | Rest:
        if any(isinstance(note, Rest) for note in self.notes): return Rest(self.duration)
        
        chord = Chord(self.notes, self.duration)
        
        # Attach slurs to the chord if underlying notes have them
        for note in self._notes:
            if note.stop_slur:
                try: attach(StopSlur(),  chord)
                except PersistentIndicatorError: pass
            if note.start_slur:
                try: attach(StartSlur(), chord)
                except PersistentIndicatorError: pass
        
        return chord
        
    def __eq__(self, other : 'RawChord') -> bool:
        return self._notes == other._notes
    
    def __add__(self, other : 'RawChord') -> 'RawChord':        
        I = self & other
        if I:
            notes_1 : List[RawNote] = sorted([note for note in self  if note in I])
            notes_2 : List[RawNote] = sorted([note for note in other if note in I])
            avg_dur : float = sum(note.time for note in notes_2) / len(notes_2)
            rest : Set[RawNote] = set(avg_dur + note for note in self - I)
            
            chord = RawChord(
                rest | set(sum(notes) for notes in zip(notes_1, notes_2)),
                self._info
            )
            
            return chord
            
        else: return self
        
    def __radd__(self, other : int) -> 'RawChord':
        return RawChord({other + note for note in self}, self._info)
    
    def __sub__(self, other : 'RawChord') -> 'RawChord':
        notes = self._notes - other._notes
        return RawChord(notes, self._info)
    
    def __and__(self, other: 'RawChord' | List['RawChord']) -> 'RawChord':
        if isinstance(other, RawChord): other = [other]
        
        I = self._notes.intersection(*[chord._notes for chord in other])
        
        return RawChord({note for note in self._notes if note in I}, self._info)
    
    def __len__(self) -> int:
        return len(self._notes)
    
    def __iter__(self) -> RawNote:
        return iter(self._notes)
    
    def __bool__(self) -> bool:
        return bool(self._notes)
    
    def __repr__(self) -> str:
        return '<' + ' '.join([repr(note) for note in self._notes]) + '>'
    
    def __str__(self) -> str:
        return ' '.join([str(note) for note in self._notes])
    
    def __bool__(self) -> bool:
        return bool(self._notes) and self.duration > 0

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

def merge_chords(chords : List[RawChord]) -> List[List[RawChord]]:
    '''Merge the chords that are repeated on consecutive frames
    and are thus to be intended as `pressed` so we should just
    mark them as having a longer duration. The idea is to scan
    the list of note-frames backwards attaching the duration
    of the note to the previous note-frame. When merging occurs,
    we build a list of lists of chords, where each inner list
    represents a single voice.
    '''
    merged = []
    
    cursor = 0
    while cursor < len(chords):
        lookahead = cursor + 1
        while chords[cursor] & chords[cursor : lookahead]:
            lookahead += 1
            if lookahead >= len(chords): break
        
        lookahead -= 1
        I = chords[cursor] & chords[cursor : lookahead]
        
        voices = [
            [sum(chords[cursor : lookahead])],
            [chord - I for chord in chords[cursor : lookahead] if chord - I]
        ]
        merged.append(voices)
        
        cursor = lookahead
    
    return merged

def fix_invalid(chords : List[RawChord]) -> List[RawChord]:
    out = []
    
    # Remove empty chords
    i = 0
    while i < len(chords) - 1:
        if chords[i]:
            out.append(chords[i])
            i += 1
        else:
            out.append(chords[i+1] + chords[i])
            i += 2
    
    # Try to patch invalid duration via merging
    # tmp = []
    # i = 0
    # while i < len(out) - 2:
    #     curr, next, post = out[i], out[i+1], out[i+2]
        
    #     # Check whether current duration is invalid
    #     try:
    #         # Trigger note creation
    #         _ = curr.abjad
    #         tmp.append(curr)
            
    #     except AssignabilityError:
    #         # This duration is invalid, try to merge with previous
    #         if curr & next and curr & post:
    #             # Incorporate the curr & post chords into the prev one
    #             tmp.append(curr + next + post)
                
    #             i += 2
    #     finally:
    #         i += 1
    
    # return tmp
    return out

def mark_sustained(chords : List[RawChord]) -> List[RawChord]:    
    i = 1
    while i < len(chords):
        prev, curr = chords[i-1], chords[i]
        
        for note in prev:
            if note in curr:
                note.start_slur = True
        for note in curr:
            if note in prev:
                note.sustained = True
                note.stop_slur = True
        i += 1
    
    return chords