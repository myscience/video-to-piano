import abjad

from typing import List, Dict

# NOTE: Sharp is coded as <note_name>s
# NOTE: Flat  is coded as <note_name>f

# duration = abjad.Duration(1, 4)
# notes = [abjad.Note(pitch, duration) for pitch in range(8)]
# staff = abjad.Staff(notes)

def get_score(
    partition : Dict[str, Dict[int, List[Dict[str, int]]]],
    minimum_unit : int = 16,
    key_signature : str = 'c',
    key_mode : str = 'major',
    clef : str | Dict[str, str] = 'treble',
    score_name : str = 'Example Score',
    manual_bar_line : bool = True,
) -> abjad.Score:
    
    bar_line = abjad.BarLine()

    key_signature = abjad.KeySignature(
        abjad.NamedPitchClass(key_signature), abjad.Mode(key_mode)
    )

    clef = abjad.Clef(name=clef)

    staffs = []
    for name, bars in partition.items():

        voices = []
        for idx, bar in bars.items():
            bar_voice = abjad.Voice([], name=f'{name}-bar:{idx}')
            for notes in bar:
                if len(notes) > 2:
                    abj_notes = [parse_note(name, duration) for name, duration in notes.items() if 'T_' not in name]

                    # Convert note to voices to allow for simultaneous playing
                    # Check whether all durations are equal, in this case we can
                    # use a chord instead of a voice
                    if len(abj_notes) == 1: voice = abj_notes[0]
                    elif all(note.written_duration == abj_notes[0].written_duration for note in abj_notes):
                        voice = abjad.Chord(*abj_notes)
                    else:
                        # Use a voice to allow for simultaneous playing
                        voice = abjad.Voice([abjad.Voice([note]) for note in abj_notes], simultaneous=True)

                else:
                    # There is no note to play, must be a rest
                    voice = parse_rest(notes['T_FRAME'])

                bar_voice.append(voice)
                
            if idx == 0:
                abjad.attach(key_signature, get_first_note(bar_voice))
                abjad.attach(clef, get_first_note(bar_voice))

            abjad.attach(bar_line, bar_voice[-1])

            voices.append(bar_voice)
            # staff.append()

        print(voices)

        staff = abjad.Staff(voices, name=name)


        if manual_bar_line:
            # Suppress automatic bar lines
            abjad.override(staff).bar_line.stencil = False
        
        # abjad.attach(bar_line, staff)
        staffs.append(staff)

    staffs = abjad.StaffGroup(staffs[::-1], lilypond_type='PianoStaff', simultaneous=True)
        
    score = abjad.Score([staffs], name=score_name)

    return score

def parse_note(
    note : str,
    duration : int,
    central_octave : int = 3,
    min_unit : int = 16,
) -> abjad.Note:
    name, octave = note.split('-')
                    
    name = name.lower().replace('#', 's').replace('b', 'f')
    pitch = "'" * (int(octave) - central_octave) + ',' * (central_octave - int(octave))

    duration = abjad.Duration(duration, min_unit)
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

def get_last_note(
    bar : abjad.Container
) -> abjad.Note:
    note = abjad.get.leaf(bar).notes[-1]

    return note