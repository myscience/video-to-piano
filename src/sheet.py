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
            for notes in bar:
                if len(notes) > 2:
                    voice = [parse_voice(name, duration) for name, duration in notes.items() if 'T_' not in name]       
                    voice = abjad.Voice(voice, simultaneous=True)

                else:
                    # There is no note to play, must be a rest
                    voice = parse_rest(notes['T_FRAME'])

                voices.append(voice)
                
            if idx == 0:
                abjad.attach(key_signature, voice[0][0])
                abjad.attach(clef, voice[0][0])


            # staff.append()

        print(voices)
        staff = abjad.Staff(voices, name=name)
        # abjad.attach(bar_line, staff)
        staffs.append(staff)

    print(staffs)
    
    score = abjad.Score(staffs, name=score_name, simultaneous=True)

    return score

def parse_voice(
    note : str,
    duration : int,
    central_octave : int = 3,
    min_unit : int = 16,
) -> abjad.Voice:
    name, octave = note.split('-')
                    
    name = name.lower().replace('#', 's').replace('b', 'f')
    pitch = "'" * (int(octave) - central_octave) + ',' * (central_octave - int(octave))

    duration = abjad.Duration(duration, min_unit)
    note = abjad.Note(name + pitch, duration)

    return abjad.Voice([note])

def parse_rest(
    duration : int,
) -> abjad.Rest():
    return abjad.Rest(duration)
