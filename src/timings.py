
from itertools import pairwise
from typing import Dict, List, Tuple

from functools import partial
from collections import defaultdict

def attach_times(
    music : Dict[str, List[List[str]]],
    times : List[float],
    fps : float = 30.,
    min_t_frame : float = .1,
) -> Dict[str, List[Dict[str, float]]]:
    '''
        This function takes care of joining the notes that are repeated
        on consecutive frames and are thus to be intended as `pressed`
        so we should just mark them as having a longer duration.
        The idea is to scan the list of note-frames backwards attaching
        the duration of the note to the previous note-frame.

        Options:
        - fps [float]: the number of frames per second, used to convert
            frame counts into seconds
    '''

    timed : Dict[str, List[Dict[str, float]]] = {}


    for hand, staff in music.items():
        # Attach a the duration (frame-count) to each note, plus
        # we add a TIME extra key to keep track of total elapsed time
        dur_staff = [
            {note : duration for note in notes + ['T_FRAME']}
                for notes, duration in zip(staff, times)
        ]

        for i, (succ, prev) in enumerate(pairwise(reversed(staff))):
            for note in succ:
                if note in prev:
                    duration = dur_staff[-i-1].pop(note)
                    dur_staff[-i-2][note] += duration

                    if len(dur_staff[-i-1]) == 1:
                        dur_staff[-i-2]['T_FRAME'] += dur_staff[-i-1]['T_FRAME']
                        dur_staff[-i-1]['T_FRAME'] = 0
                    
        timed[hand] = dur_staff

    # The ELAPSED key should provide the cumulative time, so just add them all
    for hand, staff in timed.items():

        staff[0]['T_ELAPSED'] = 0

        for i in range(1, len(staff)):
            staff[i]['T_ELAPSED'] = staff[i-1]['T_ELAPSED'] + staff[i-1]['T_FRAME']
    
    # Convert frame counts to seconds using fps
    timed = {hand : [{name : dur / fps for name, dur in notes.items()} for notes in staff
                     if len(notes) > 2 or notes['T_FRAME'] / fps > min_t_frame]
                for hand, staff in timed.items()}
    
    # Merge adjacent frames if T_FRAME too short
    for hand in timed:
        flags = []
        for pred, succ in pairwise(range(len(timed[hand]))):
            if timed[hand][pred]['T_FRAME'] < min_t_frame:
                timed[hand][succ].update({k : v for k, v in timed[hand][pred].items() if 'T_' not in k})
                timed[hand][succ]['T_FRAME']   += timed[hand][pred]['T_FRAME']
                timed[hand][succ]['T_ELAPSED'] += timed[hand][pred]['T_FRAME']
                flags.append(pred)
        
        timed[hand] = [v for idx, v in enumerate(timed[hand]) if idx not in flags]

    return timed

def get_partition(
    timed_music : Dict[str, List[Dict[str, float]]],
    quarter_bpm : float = 60,
    global_time : Tuple[int, int] = (4, 4),
    minimum_unit : int = 16,
) -> Dict[str, List[Dict[str, int]]]:
    '''
        This function tries to map raw elapsed seconds for which a note
        is played into a formal note duration. To do so it requires the
        quarter beat-per-minute which is used a reference. Furthermore,
        a global time can be specified in which case the function tries
        to segment the music into music bars.
    '''
    
    convert = partial(sec2unit, min_unit=minimum_unit, quarter_bpm=quarter_bpm)

    partition = {hand : [{name : convert(note) for name, note in notes.items()}
                            for notes in staff]
                    for hand, staff in timed_music.items()
                }

    # Now that everything is expressed into unit of `minimum_unit` we
    # can attempt to subdivide the music into bars
    units_per_bar, time_unit = global_time
    units_per_bar *= minimum_unit / time_unit

    for hand, staff in partition.items():
        bar_notes = defaultdict(list)

        for notes in staff:
            # If a note dic has less than two entries it means it only has
            # T_FRAME and T_ELAPSE, meaning no note is played, so this must
            # be a rest of length equal to T_FRAME, so just put it there!
            if len(notes) < 3: notes.update({'REST-' : notes['T_FRAME']})
            bar_notes[int(notes['T_ELAPSED'] // units_per_bar)].append(notes)            

        # Check each bar boundary for incorrect partitioning, if found attempt
        # note duration splitting
        for prev, post in pairwise(range(len(bar_notes))):
            prev_notes = bar_notes[prev]
            post_notes = bar_notes[post]

            prev_duration = get_bar_duration(prev_notes)
            post_duration = get_bar_duration(post_notes)

            prev_correct = prev_duration == units_per_bar
            post_correct = post_duration == units_per_bar

            # Check whether we should move the boundary to the left
            # (i.e. post bar is too short and prev bar is too long)
            # or the opposite.
            prev_diff = int(units_per_bar - prev_duration)
            post_diff = int(units_per_bar - post_duration)

            move_bar_left  = not prev_correct and not post_correct and prev_duration < post_duration
            move_bar_right = not prev_correct and not post_correct and prev_duration > post_duration
            if   move_bar_left : prev_notes.append(   remove_note_duration(post_notes[+0], post_diff))
            elif move_bar_right: post_notes.insert(0, remove_note_duration(prev_notes[-1], prev_diff))
            else: pass

            if prev_notes[-1]['T_FRAME'] < 1: del prev_notes[-1]
            if post_notes[+0]['T_FRAME'] < 1: del post_notes[+0]

            bar_notes[prev] = prev_notes
            bar_notes[post] = post_notes

        bars = {}
        for idx, notes in bar_notes.items():
            bar_duration = sum([note['T_FRAME'] for note in notes])
            bars[idx] = {
                'notes' : notes,
                'duration' : bar_duration,
                'correct' : bar_duration == units_per_bar
            }

        partition[hand] = bars

    return partition

def sec2unit(
    dur_sec : float,
    min_unit : int = 16,
    quarter_bpm : float = 60,    
):
    base = 4 / min_unit
    num_quart = dur_sec * quarter_bpm / 60

    return round(num_quart / base)

def remove_note_duration(note : Dict[str, int], diff_dur : int) -> Dict[str, int]:
    '''
        This function takes a note and a difference in duration and
        modifies the note in-place so to remove the provided duration.
        It additionally returns a new note representing the modified
        duration.
    '''

    new_note = {k : abs(diff_dur) for k, v in note.items()}
    
    # Here we modify the new note in-place
    for k in note: note[k] += diff_dur

    return new_note

def get_bar_duration(notes : List[Dict[str, int]]) -> int:
    return sum([note['T_FRAME'] for note in notes])