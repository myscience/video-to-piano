import abjad
from abjad import attach
from copy import copy, deepcopy
from abjad import Duration
from abjad import Note, Chord, Rest
from abjad import StartSlur, StopSlur
from abjad import Voice, Staff, StaffGroup, Score
from abjad import MetronomeMark

from abjad.get import duration as get_duration

from functools import partial
from itertools import pairwise
from collections import defaultdict
from typing import List, Dict, Tuple

from .misc import lazydefault
from .misc import invert_dict
from .misc import get_note_name

from tqdm.auto import trange

MAX_NUMBER_OF_VOICES = 15
MAX_DURATIONS_SPLITS = 10

# NOTE: Sharp is coded as <note_name>s
# NOTE: Flat  is coded as <note_name>f

# duration = abjad.Duration(1, 4)
# notes = [abjad.Note(pitch, duration) for pitch in range(8)]
# staff = abjad.Staff(notes)

def get_score(
    partition : Dict[str, Dict[int, List[Dict[str, int]]]],
    key : str | Dict[int, str] = 'c',
    time : Tuple[int, int] | Dict[int, Tuple[int, int]] = (4, 4),
    clef : str | Dict[str, str] | Dict[str, Dict[int, str]] = 'treble',
    key_mode : str = 'major',
    score_name : str = 'Example Score',
    minimum_unit : int = 16,
    drop_last_bar : bool = True,
    metronome_note : Tuple[int, int] = (1, 4),
    metronome_beat : int = 60,
    metronome_feel : str | None = None,
    verbose : bool = False,
) -> Score:
    
    if isinstance(clef, str): clef = {name : clef for name in partition}
    if isinstance(key, str): key_signature = {0 : key}
    if isinstance(time, tuple): time_signature = {0 : time}

    # ! FIXME: THIS MIGHT LEAD TO TROUBLE IN THE FUTURE
    num_bars = min(len(bar) - 1 for bar in partition.values())

    staffs = []

    for name, bars in partition.items():
        staff_clef = clef[name]
        if isinstance(staff_clef, str): staff_clef = {0 : staff_clef}

        voices = []
        iterator = trange(num_bars - drop_last_bar, desc=f'Creating score | {name.capitalize()}')\
                    if verbose else range(num_bars - drop_last_bar)
    
        for idx in iterator:
            bar = bars[idx]

            try:
                bar_duration = Duration(time_signature[idx])
            except KeyError:
                bar_duration = Duration(time)

            # bar_voice = abjad.Voice([], name=f'{name}-bar:{idx}')
            bar_voices : Dict[int, Voice] = defaultdict(partial(new_voice, name=f'{name}-bar:{idx}-component'))

            for notes in bar['notes']:
                # * NOTE: Voices can be separated by checking whether the two consecutive notes have
                # *       the same duration (i.e. this is a chord) or they are played at the same time
                # *       but with different durations (i.e. they are two separate voices)

                # Invert the notes dictionary, indexing via the duration, this is most
                # useful to identify whether we need a note, a chord or two voices
                inv_notes = invert_dict(notes, exclude='T_')

                try:
                    abj_notes = [parse_notes(chord, duration) for duration, chord in inv_notes.items()]
                    abj_notes = [note for notes in abj_notes for note in notes]

                except abjad.AssignabilityError as err:
                    msg = f'Caught an Assignability Error: {err}.\nError occurred in bar #{idx} while parsing {inv_notes}'
                    raise abjad.AssignabilityError(msg)

                # Add each note to a different voice if voice duration is below bar measure
                curr_voice = 0
                curr_note = abj_notes[0]

                while len(abj_notes):
                    note_duration = get_duration(curr_note)
                    voice_duration = get_duration(bar_voices[curr_voice])
                    
                    if note_duration + voice_duration <= bar_duration:
                        bar_voices[curr_voice].append(curr_note)
                        curr_note = abj_notes.pop()
                        curr_voice += 1
                    else:
                        curr_voice += 1

                    if curr_voice > MAX_NUMBER_OF_VOICES:
                        raise ValueError('Runaway number of voices')
                    
                # clean_voices = add_slurs(bar_voices, minimum_unit=minimum_unit)
                clean_voices = bar_voices.values()
                    
            bar_voices = Voice(clean_voices, name=f'{name}-bar:{idx}', simultaneous=True)

            # Attach the decorators
            if idx in staff_clef:
                abj_clef = abjad.Clef(name=staff_clef[idx])
                attach(abj_clef, get_first_note(bar_voices))

            if idx in key_signature:
                abj_key = abjad.KeySignature(
                    abjad.NamedPitchClass(key_signature[idx]), abjad.Mode(key_mode)
                )
   
                attach(abj_key, get_first_note(bar_voices))

            if idx in time_signature:
                abj_time = abjad.TimeSignature(time_signature[idx])
                metronome_note = Duration(metronome_note)

                abj_mark = MetronomeMark(
                    reference_duration = metronome_note,
                    units_per_minute   = metronome_beat,
                    textual_indication = metronome_feel,
                )

                attach(abj_time, get_first_note(bar_voices))
                attach(abj_mark, get_first_note(bar_voices))

            voices.append(bar_voices)

        staff = Staff(voices, name=name)
        
        staffs.append(staff)

    if verbose: iterator.set_description('Rewriting meter')

    # By rewriting the meter we unsure that slurs are favored
    # over the uglier dot-notation
    meter = abjad.Meter(time, preferred_boundary_depth=0)
    abjad.Meter.rewrite_meter(staffs, meter)

    if verbose: iterator.set_description('Completed')    

    staffs = StaffGroup(staffs[::-1], lilypond_type='PianoStaff', simultaneous=True)
        
    score = Score([staffs], name=score_name)

    return score

def new_voice(
    name : str = 'default'
) -> Voice:
    return Voice([], name=name)

def add_slurs(
    bar : Dict[int, Voice],
    minimum_unit : int = 16,
) -> List[Voice]:
    # Scan notes to find slur start and stop
    clean_voices = []
    for voice in bar.values():
        clean_voice = Voice([], name=f'{voice.name}-cleaned')

        for i, note in enumerate(voice):
            curr_note = copy(note)
            prev_note = lazydefault(lambda : clean_voice[i - 1], err = None)
            prev_duration = get_duration(prev_note) if prev_note else Duration(0, minimum_unit) 
            curr_duration = get_duration(curr_note)

            size, unit = curr_duration.numerator, curr_duration.denominator

            if size > 1 and Duration(1, unit) == prev_duration:
                # Separate the longer odd note into a sustained note and the rest
                mute_note : Note | Chord = copy(curr_note)
                next_note : Note | Chord = copy(curr_note)

                mute_note.written_duration = Duration(       1, unit)
                next_note.written_duration = Duration(size - 1, unit)

                attach(StartSlur(), mute_note)
                attach(StopSlur(),  next_note)

                clean_voice.extend((mute_note, next_note))
            else:
                clean_voice.append(curr_note)

        clean_voices.append(clean_voice)

    return clean_voices

def parse_notes(
    codes : str | List[str],
    duration : int,
    central_octave : int = 3,
    min_unit : int = 16,
) -> List[Note | Chord]:
    duration = Duration(duration, min_unit)

    durations = [duration.equal_or_lesser_assignable]
    num_dur_splits = 0
    while (duration := duration - duration.equal_or_lesser_assignable) > 0:
        durations.append(duration)
        num_dur_splits += 1

        if num_dur_splits > MAX_DURATIONS_SPLITS:
            raise ValueError('Runaway number of duration splits')

    if isinstance(codes, str): codes = [codes]

    parsed = []
    for duration in durations:
        notes = [_rest_or_note(
                note,
                duration=duration,
                central_octave=central_octave,
                min_unit=min_unit,
            ) for note in codes
        ]

        out = _promote_to_chord(notes)

        parsed.append(out)

    return parsed

def _rest_or_note(
    code : str,
    duration : int | Duration,
    min_unit : int = 16,
    central_octave : int = 3,
) -> Rest | Note:
    if isinstance(duration, int): duration = Duration(duration, min_unit)

    name, octave = code.split('-')

    if name == 'REST': note = Rest(duration)
    else:
        name = name.replace('#', 's').replace('b', 'f').lower()
        pitch = "'" * (int(octave) - central_octave) + ',' * (central_octave - int(octave))

        note = Note(name + pitch, duration)

    return note

def _promote_to_chord(
    notes : List[Note],
) -> Note | Chord:
    duration = notes[0].written_duration
    all_equal = all([note.written_duration == duration for note in notes])

    if len(notes) == 1: return notes[0]
    elif len(notes) > 1 and all_equal:
        chord = Chord('<' + ' '.join([get_note_name(note) for note in notes]) + '>')
        chord.written_duration = duration
        return chord
    else:
        raise ValueError(f'Unknown promotion rule for notes: {notes}')

def parse_rest(
    duration : int,
    min_unit : int = 16,
) -> Rest:
    duration = Duration(duration, min_unit)

    return Rest(duration)

def get_first_note(
    bar : abjad.Container
) -> abjad.Note:
    return abjad.get.leaf(bar, n=0)

def get_last_note(
    bar : abjad.Container
) -> abjad.Note:
    return abjad.get.leaf(bar, n=-1)