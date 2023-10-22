
from itertools import pairwise
from typing import Dict, List


def condense_equal(
    music : Dict[str, List[List[str]]],
    times : List[float],
) -> Dict[str, List[List[str]]]:
    '''
        This function takes care of joining the notes that are repeated
        on consecutive frames and are thus to be intended as `pressed`
        so we should just mark them as having a longer duration.
        The idea is to scan the list of note-frames backwards attaching
        the duration of the note to the previous note-frame.
    '''

    condensed = {}


    for hand, staff in music.items():
        # Attach a duration of 1 for each note
        dur_staff = [
            {note : duration for note in notes}
                for notes, duration in zip(staff, times)
        ]

        print(dur_staff)

        for i, (succ, prev) in enumerate(pairwise(reversed(staff))):
            for note in succ:
                if note in prev:
                    duration = dur_staff[-i-1][note]
                    dur_staff[-i-2][note] += duration

                    dur_staff[-i-1].pop(note)
                    
        condensed[hand] = dur_staff

    return condensed
