from dataclasses import dataclass, field
from copy import copy, deepcopy

from abjad import Note, Rest, Chord, attach, StartSlur, StopSlur
from abjad import Duration, PersistentIndicatorError, AssignabilityError

from .utils.misc import Configs, Notes
from .utils.misc import NOTE_ORDER, to_ms

from typing import List, Set

@dataclass
class RawNote:
    name : Notes
    time : float
    info : Configs = field(default_factory=Configs)
    
    sustained  : bool = False
    stop_slur  : bool = False
    start_slur : bool = False
    
    @property
    def duration(self) -> Duration:
        value = round(
            self.time / (self.info.SEC_IN_MIN / self.info.BPM /
            (self.info.MIN_UNIT / self.info.BPM_UNIT) * self.info.MS_IN_SEC)
        )
        
        return Duration(
            value,
            self.info.MIN_UNIT
        )
    
    @property
    def valid(self) -> bool:
        try:
            _ = Note('a', self.duration)
            return True
        except AssignabilityError:
            return False
    
    @property
    def exist(self) -> bool:
        return self.duration > 0
    
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
        if not self.valid: sym = '⛔️'
        if not self.exist: sym = '💀'
        
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
        time : float | Duration = 0,
        
        # FIXME: Missing support for __add__ for elapsed
        elapsed : float = 0,
        
    ) -> None:
        info = info or Configs()
        if isinstance(time, Duration):
            # Convert duration to ms
            n, d = time.pair
            time = to_ms(time, info)
        if isinstance(notes, str)    : notes = set([RawNote(notes, time)])
        if isinstance(notes, list)   : notes = set([RawNote(note,  time) for note in notes])
        if isinstance(notes, RawNote): notes = set([notes])
        
        self._notes = notes
        self._info  = info
        self.elapsed = elapsed
        
        for note in self._notes: note.info = info
        # for note in self._notes: note.time = time
    
    @property
    def notes(self) -> List[Note]:
        return [note.abjad for note in self._notes if note.duration > 0]
    
    @property
    def duration(self) -> Duration:
        return Duration(
            max([note.duration for note in self._notes])
        )
    
    @property
    def time(self) -> float:
        return sum([note.time for note in self._notes]) / len(self._notes)
    
    @property
    def valid(self) -> bool:
        return all(note.valid for note in self._notes)
    
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
    
    def set_time(self, time : float) -> None:
        for note in self._notes: note.time = time
        
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
                self._info,
                elapsed=self.elapsed + to_ms(other.duration, self._info)
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
        
        return RawChord(
            {note for note in self._notes if note in I},
            self._info,
            elapsed=self.elapsed
        )
    
    def __len__(self) -> int:
        return len(self._notes)
    
    def __iter__(self) -> RawNote:
        return iter(self._notes)
    
    def __bool__(self) -> bool:
        return bool(self._notes)
    
    def __repr__(self) -> str:
        return '<' + ' '.join([repr(note) for note in self._notes]) + '>'
    
    def __str__(self) -> str:
        tot_sec = self.elapsed / 1e3
        minutes = str(int(tot_sec // 60))  
        seconds = str(int(tot_sec %  60)).zfill(2) 
        return f'{minutes}m {seconds}s | ' + ' '.join([str(note) for note in self._notes])
    
    def __bool__(self) -> bool:
        return bool(self._notes) and self.duration > 0

def fix_invalid(
    chords : List[RawChord],
    remove_empty  : bool = True,
    merge_invalid : bool = True,
    split_invalid : bool = True,
) -> List[RawChord]:
    # Initialize the output list to the provided chords
    out = copy(chords)
    
    # * Try to patch invalid duration via merging
    tmp : List[RawChord] = []
    i = 0
    while merge_invalid and i < len(chords) - 2:
        curr, next, post = chords[i], chords[i+1], chords[i+2]
        
        # This duration is invalid, try to merge with previous
        if not curr.valid and next.valid and post.valid and curr & next and curr & post:
            # Incorporate the curr & post chords into the prev one
            tmp.append(next.time + post.time + curr)
            i += 2
        else: tmp.append(curr)
        
        i += 1
    else:
        out = tmp
    
    # * Remove empty chords
    tmp : List[RawChord] = []
    
    i = 0
    while remove_empty and i < len(out) - 1:
        if out[i]:
            tmp.append(out[i])
        else:
            out[i+1] += out[i]
        i += 1
    else:
        out = tmp
    
    # * Split invalid chords
    if split_invalid:
        tmp = []
        for chord in out:
            if not chord.valid:
                d1 = chord.duration.equal_or_lesser_power_of_two
                d2 = chord.duration - d1
                
                t1 = to_ms(d1, chord._info)
                t2 = to_ms(d2, chord._info)
                
                chord1 = deepcopy(chord)
                chord2 = deepcopy(chord)
                
                for note in chord1._notes: note.time = t1
                for note in chord2._notes: note.time = t2
                
                tmp.append(chord1)
                tmp.append(chord2)
            else:
                tmp.append(chord)
        out = tmp
    
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