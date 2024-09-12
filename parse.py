from argparse import ArgumentParser
from argparse import Namespace
import json

from abjad import BarLine, Clef, Duration, KeySignature, LilyPondFile, Meter, MetronomeMark, Mode, NamedPitchClass, Score, Staff, StaffGroup, Voice, attach, show

from parser import extract_notes
from parser import fix_invalid
from parser.utils import get_layout
from parser.utils import Color, get_leaf
from parser.utils import Configs, BLUE, GREEN

DEFAULT_CLEFS = {
    'left' : {0, 'bass'},
    'right': {0, 'treble'},
}

DEFAULT_NOTES = {
    'left' : BLUE,
    'right': GREEN,
}

def main(args : Namespace) -> None:
    
    report = print if args.verbose else lambda *a, **k: None
    
    # Create the overall configuration
    config = Configs(
        BPM            = args.bpm,
        BPM_UNIT       = args.bpm_unit,
        MIN_UNIT       = args.min_unit,
        time_signature = args.time_signature,
        central_octave = args.central_octave,
        start_octave   = args.start_octave,
        num_octaves    = args.num_octaves,
        first_note     = args.first_note,
        last_note      = args.last_note,
        notation       = args.notation,
    )
    
    # Get the layout of the keys
    layout = get_layout(config)
    
    # * Extract the notes from the video
    music, info, frames = extract_notes(
        args.path,
        layout,
        note_color=args.note_color,
        configs=config,
        skip_intro=args.skip_intro,
        skip_outro=args.skip_outro,
        early_stop=args.early_stop,
        trim_areas=(slice(*args.trim_width), slice(*args.trim_height)),
        verbose=args.verbose,
    )
    
    frame_height, frame_width = next(iter(frames.values()))[0].shape[:2]
    number_frames = {key : len(value) for key, value in frames.items()}
    notes_onsets  = {key : f'{val:.3f}' for key, val in info['notes_onset'].items()}
    notes_offsets = {key : f'{val:.3f}' for key, val in info['notes_offset'].items()}
    report(f'Notes extraction returned the following information:')
    report(f'Hands:            {len(music)}')
    report(f'Parsed Frames:    {number_frames}')
    report(f'Video FPS:        {info["video_fps"]:.3f}')
    report(f'Video Duration:   {info["video_frame_count"]}')
    report(f'Video Explored:   {info["video_fraction"]:.2%}')
    report(f'Video Resolution: {info["video_frame_width"]}x{info["video_frame_height"]}')
    report(f'Video Slice:      {frame_width}x{frame_height}')
    report(f'Notes Onset:      {notes_onsets}')
    report(f'Notes Offset:     {notes_offsets}')
    report(f'Detected Notes:   {info["detected_chords"]}')
    
    # * Fix invalid notes via pruning & merging
    for hand in music:
        music[hand] = fix_invalid(music[hand])
    
    # Check that all notes in the music are valid
    for hand, chord in music.items():
        for note in chord: assert note.valid, f'Invalid note: {str(note)} | On music: {hand}'
    
    # * Create the Abjad Voice & Staves
    staves = {
        hand : Staff([
                Voice([
                    chord.abjad for chord in voice if chord
                ],
                name=f'{hand} Voice'),
            ], name=f'{hand} Staff'
        )
        for hand, voice in music.items()
    }
    
    # Rewrite meter if requested by user
    if args.rewrite:
        meter = Meter(args.time_signature, preferred_boundary_depth=args.boundary_depth)
        for hand in args.rewrite:
            Meter.rewrite_meter(staves[hand], meter)
    
    # * Create the Abjad Score
    key_signature = KeySignature(NamedPitchClass(args.key), Mode(args.mode))
    bpm_signature = MetronomeMark(
        reference_duration = Duration((1, args.bpm_unit)),
        units_per_minute   = args.bpm,
        textual_indication = args.mood,
    )
    
    # Add indicators to the staves
    for hand, staff in staves.items():
        attach(key_signature, get_leaf(staff))
        attach(bpm_signature, get_leaf(staff))
        attach(BarLine('|.'), staff[-1][-1])
    
    for hand, clefs in args.clefs.items():
        for bar, clef in clefs.items():
            attach(Clef(clef), get_leaf(staves[hand][0][int(bar)]))
    
    group = StaffGroup(
        list(staves.values())[::-1],
        name='Piano Staff Group',
        lilypond_type='PianoStaff',
        simultaneous=True
    )
    score = Score([group], name=f'Piano Score - {args.name}')
    
    preamble = fr'''
        # (set-global-staff-size 20)
        \header {{
            composer = \markup {{ {args.composer} }}
            subtitle = \markup {{ {args.subtitle} }}
            title = \markup {{ {args.title} }}
            tagline = "{args.tagline}"
        }}

        \layout {{
            indent = 0
        }}
    '''
    
    # * Create the LilyPond file & render the score
    file = LilyPondFile([preamble, score])
    
    show(
        file,
        output_directory=args.out_dir,
        render_prefix=args.out_name,
        should_open=args.open,
    )
    
if __name__ == '__main__':
    parser = ArgumentParser()
    
    parser.add_argument('path', type=str, help='Path to the video file to parse for score.')
    
    # Arguments for the configuration
    parser.add_argument('--skip_intro',     type=int, help='Number of intro frames to skip.', default=None)
    parser.add_argument('--skip_outro',     type=int, help='Number of outro frames to skip.', default=None)
    parser.add_argument('--early_stop',     type=int, help='Number of frames to stop early.', default=None)
    parser.add_argument('--bpm',            type=int, help='Beats per minute.', default=60)
    parser.add_argument('--bpm_unit',       type=int, help='Unit of beats per minute.', default=4)
    parser.add_argument('--min_unit',       type=int, help='Minimum unit of duration.', default=16)
    parser.add_argument('--central_octave', type=int, help='Central octave of the piano.', default=3)
    parser.add_argument('--start_octave',   type=int, help='Starting octave of the piano.', default=1)
    parser.add_argument('--num_octaves',    type=int, help='Number of octaves in the piano.', default=7)
    parser.add_argument('--first_note',     type=str, help='First note of the piano.', default='D')
    parser.add_argument('--last_note',      type=str, help='Last note of the piano.', default='G')
    parser.add_argument('--notation',       type=str, help='Notation style.', choices=['flat', 'sharp'], default='flat')
    parser.add_argument('--time_signature', type=str, help='Time signature of the score.', default=(4, 4), nargs='+')
    
    parser.add_argument('--note_color',  type=str, help='Color of the notes to extract.', default=DEFAULT_NOTES)
    parser.add_argument('--trim_width',  type=int, help='Slice start-end to trim frame along width dimension.', default=(-250, None), nargs=2)
    parser.add_argument('--trim_height', type=int, help='Slice start-end to trim frame along width dimension.', default=(None, None), nargs=2)
    
    # Arguments for the score
    parser.add_argument('--clefs',    type=str, help='Clefs for each hand.', default=DEFAULT_CLEFS)
    parser.add_argument('--rewrite',  type=str, help='Rewrite the meter for the given hand.', default=[], nargs='+')
    parser.add_argument('--key',      type=str, help='Key signature of the score.', default='C')
    parser.add_argument('--mode',     type=str, help='Mode of the score.', choices=['major', 'minor'], default='major')
    parser.add_argument('--mood',     type=str, help='Textual indication of beats per minute.', default='')
    parser.add_argument('--composer', type=str, help='Composer of the score.', default='')
    parser.add_argument('--subtitle', type=str, help='Subtitle of the score.', default='')
    parser.add_argument('--title',    type=str, help='Title of the score.', default='')
    parser.add_argument('--tagline',  type=str, help='Tagline of the score.', default='')
    parser.add_argument('--name',     type=str, help='Name of the score.', default='Untitled')
    parser.add_argument('--out_dir',  type=str, help='Output directory for the rendered score.', default='.')
    parser.add_argument('--out_name', type=str, help='Output name for the rendered score.', default='score')
    parser.add_argument('--boundary_depth', type=int, help='Preferred boundary depth for meter rewriting.', default=1)
    
    parser.add_argument('--open',    action='store_true', help='Open the rendered score after rendering.')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output.')
    
    args = parser.parse_args()
    
    # Parse the dictionary from the string
    if isinstance(args.clefs,      str): args.clefs      = json.loads(args.clefs)
    if isinstance(args.note_color, str): args.note_color = json.loads(args.note_color)
    if isinstance(args.time_signature, list): args.time_signature = tuple([int(x) for x in args.time_signature])
    args.note_color = { hand : Color.from_str(color) for hand, color in args.note_color.items() }

    main(args)