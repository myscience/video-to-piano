import abjad

from abjad import attach
from abjad import StartSlur, StopSlur

from functools import partial
from collections import defaultdict
from typing import List, Dict, Tuple

from .misc import invert_dict

MAX_NUMBER_OF_VOICES = 15

# NOTE: Sharp is coded as <note_name>s
# NOTE: Flat  is coded as <note_name>f

# duration = abjad.Duration(1, 4)
# notes = [abjad.Note(pitch, duration) for pitch in range(8)]
# staff = abjad.Staff(notes)

def get_score(
    partition : Dict[str, Dict[int, List[Dict[str, int]]]],
    minimum_unit : int = 16,
    time : Tuple[int, int] | Dict[int, Tuple[int, int]] = (4, 4),
    key : str | Dict[int, str] = 'c',
    key_mode : str = 'major',
    clef : str | Dict[str, str] | Dict[str, Dict[int, str]] = 'treble',
    score_name : str = 'Example Score',
) -> abjad.Score:
    
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
        for idx in range(num_bars):
            bar = bars[idx]

            try:
                bar_duration = abjad.Duration(time_signature[idx])
            except KeyError:
                bar_duration = abjad.Duration(time)

            # bar_voice = abjad.Voice([], name=f'{name}-bar:{idx}')
            bar_voices = defaultdict(partial(new_voice, name=f'{name}-bar:{idx}-component'))

            for notes in bar:
                # Invert the notes dictionary, indexing via the duration, this is most
                # useful to identify whether we need a note, a chord or two voices
                inv_notes = invert_dict(notes, exclude='T_')
                # abj_notes = [parse_note(name, duration) for name, duration in notes.items() if 'T_' not in name]
                abj_notes = [parse_notes(chord, duration) for duration, chord in inv_notes.items()]

                # Add each note to a different voice if voice duration is below bar measure
                curr_voice = 0
                curr_note = abj_notes[0]

                while len(abj_notes):
                    voice_duration = abjad.get.duration(bar_voices[curr_voice])
                    
                    if abjad.get.duration(curr_note) + voice_duration <= bar_duration:
                        bar_voices[curr_voice].append(curr_note)
                        curr_note = abj_notes.pop()
                        curr_voice += 1
                    else:
                        curr_voice += 1

                    if curr_voice > MAX_NUMBER_OF_VOICES:
                        raise ValueError('Runaway number of voices')
                
            bar_voices = abjad.Voice(bar_voices.values(), name=f'{name}-bar:{idx}', simultaneous=True)

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

                attach(abj_time, get_first_note(bar_voices))

            voices.append(bar_voices)

        staff = abjad.Staff(voices, name=name)
        
        # abjad.attach(bar_line, staff)
        staffs.append(staff)

    staffs = abjad.StaffGroup(staffs[::-1], lilypond_type='PianoStaff', simultaneous=True)
        
    score = abjad.Score([staffs], name=score_name)

    return score

def new_voice(
    name : str = 'default'
) -> abjad.Voice:
    return abjad.Voice([], name=name)

def parse_notes(
    notes : str | List[str],
    duration : int,
    central_octave : int = 3,
    min_unit : int = 16,
) -> abjad.Note | abjad.Chord:
    duration = abjad.Duration(duration, min_unit)

    if isinstance(notes, str): notes = [notes]

    parsed = []
    for note in notes:
        name, octave = note.split('-')

        if name == 'REST': return abjad.Rest(duration)
                        
        name = name.replace('#', 's').replace('b', 'f').lower()
        pitch = "'" * (int(octave) - central_octave) + ',' * (central_octave - int(octave))

        # note = abjad.Note(name + pitch, duration)
        # parsed.append(note)
        note = name + pitch# + duration
        parsed.append(note)

    if len(parsed) > 1:
        out = abjad.Chord('<' + ' '.join(parsed) + '>')
        out.written_duration = duration
    else: out = abjad.Note(parsed[0], duration)

    return out    

def parse_rest(
    duration : int,
    min_unit : int = 16,
) -> abjad.Rest():
    duration = abjad.Duration(duration, min_unit)

    return abjad.Rest(duration)

def get_first_note(
    bar : abjad.Container
) -> abjad.Note:
    return abjad.get.leaf(bar, n=0)

def get_last_note(
    bar : abjad.Container
) -> abjad.Note:
    return abjad.get.leaf(bar, n=-1)