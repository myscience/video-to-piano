import abjad

from functools import partial
from collections import defaultdict
from typing import List, Dict, Tuple

# NOTE: Sharp is coded as <note_name>s
# NOTE: Flat  is coded as <note_name>f

# duration = abjad.Duration(1, 4)
# notes = [abjad.Note(pitch, duration) for pitch in range(8)]
# staff = abjad.Staff(notes)

def get_score(
    partition : Dict[str, Dict[int, List[Dict[str, int]]]],
    minimum_unit : int = 16,
    time_signature : Tuple[int, int] = (4, 4),
    key_signature : str = 'c',
    key_mode : str = 'major',
    clef : str | Dict[str, str] = 'treble',
    score_name : str = 'Example Score',
) -> abjad.Score:
    
    bar_line = abjad.BarLine()

    key_signature = abjad.KeySignature(
        abjad.NamedPitchClass(key_signature), abjad.Mode(key_mode)
    )

    clef = abjad.Clef(name=clef)
    time_signature = abjad.TimeSignature(time_signature)
    bar_duration = abjad.Duration(time_signature)

    staffs = []
    for name, bars in partition.items():

        voices = []
        for idx, bar in bars.items():
            # bar_voice = abjad.Voice([], name=f'{name}-bar:{idx}')
            bar_voices = defaultdict(partial(new_voice, name=f'{name}-bar:{idx}-component'))

            for notes in bar:
                abj_notes = [parse_note(name, duration) for name, duration in notes.items() if 'T_' not in name]

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

                    if curr_voice > 15:
                        raise ValueError('Runaway number of voices')
                
            bar_voices = abjad.Voice(bar_voices.values(), name=f'{name}-bar:{idx}', simultaneous=True)

            if idx == 0:
                abjad.attach(clef, get_first_note(bar_voices))
                abjad.attach(key_signature, get_first_note(bar_voices))
                abjad.attach(time_signature, get_first_note(bar_voices))

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

def parse_note(
    note : str,
    duration : int,
    central_octave : int = 3,
    min_unit : int = 16,
) -> abjad.Note:
    duration = abjad.Duration(duration, min_unit)

    name, octave = note.split('-')

    if name == 'REST': return abjad.Rest(duration)
                    
    name = name.lower().replace('#', 's').replace('b', 'f')
    pitch = "'" * (int(octave) - central_octave) + ',' * (central_octave - int(octave))

    note = abjad.Note(name + pitch, duration)

    return note

def parse_rest(
    duration : int,
    min_unit : int = 16,
) -> abjad.Rest():
    duration = abjad.Duration(duration, min_unit)

    return abjad.Rest(duration)

def get_first_note(
    bar : abjad.Container
) -> abjad.Note:
    return abjad.get.leaf(bar)

# ! FIXME: This routine is broken
def get_last_note(
    bar : abjad.Container
) -> abjad.Note:
    note = abjad.get.leaf(bar).notes[-1]

    return note