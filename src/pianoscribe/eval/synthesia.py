"""Ground-truth notes from Synthesia-style videos (colored bars falling onto a keyboard).

The falling-notes area is a tape scrolling down at a constant `v` px/frame. A pixel at row
y in frame f reaches the hit line (y_line - y)/v frames later, so every sample maps to the
tape position  u = f*v + (y_line - y)  [px]  which hits the line at time  u / (v*fps).
Stitching a band of rows from every frame rebuilds the tape at 1 px resolution (~2.6 ms at
13 px/frame, 29.97 fps): fine enough to see the few-px gaps that separate repeated notes,
which sampling the lit keys once per frame (33 ms) would miss.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..transcribe.base import Hand, NoteEvent, Transcription

WHITE_SEMITONES = (0, 2, 4, 5, 7, 9, 11)  # C D E F G A B
HAS_BLACK_AFTER = (True, True, False, True, True, True, False)  # C# D# . F# G# A# .


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    n_frames: int


@dataclass(frozen=True)
class Key:
    x: float  # column of the key (and of its falling bars)
    pitch: int
    black: bool


@dataclass(frozen=True)
class Style:
    """How a falling-notes video draws things (see STYLES)."""

    name: str
    hit_line: str  # "red": Synthesia's red line | "keyboard": just above the white-key band
    keys: str  # "detect": separators + black keys of an idle keyboard | "piano88": a full keyboard fitted to them
    band: str  # where the tape is read: "above_line" (just above the hit line) | "top" (top of the screen)
    lit: str  # "synthesia": pastel white-key / saturated black-key bars | "bright_red": bars red + bright
    hands_by_color: bool  # blue = left, green = right
    bar_lines: bool  # faint full-width lines at each downbeat


STYLES = {
    # Synthesia tutorials ('exile'): red hit line, bar colour = hand, bar lines.
    "synthesia": Style("synthesia", "red", "detect", "above_line", "synthesia", True, True),
    # PianoX-style covers ('never-gonna-give-you-up'): a filmed pianist's hands cover the keys, bar
    # colour fades red -> purple -> blue with height (time, not hand) and blue particle effects
    # swirl above the keyboard. At the very top bars are red and bright, the effects blue and dim.
    "pianox": Style("pianox", "keyboard", "piano88", "top", "bright_red", False, False),
}


def probe(video: Path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
         "stream=width,height,r_frame_rate,nb_read_packets", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    num, den = map(int, s["r_frame_rate"].split("/"))
    return VideoInfo(s["width"], s["height"], num / den, int(s["nb_read_packets"]))


def read_frame(video: Path, info: VideoInfo, index: int) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", f"{index / info.fps:.4f}", "-i", str(video),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, np.uint8).reshape(info.height, info.width, 3)


def stream_band(video: Path, info: VideoInfo, y0: int, rows: int, start: float = 0.0) -> Iterator[np.ndarray]:
    """Yield the (rows, width, 3) band starting at row y0, for consecutive frames.

    Frames within one stream are exact neighbours; seeking (`start` > 0, or `read_frame`) is
    NOT frame-accurate on every file, so anything needing exact frame indices streams from 0.
    """
    # Convert to RGB *before* cropping: cropping yuv420p at odd rows either gets silently rounded
    # to even (fixed-size reads then drift out of frame alignment) or, with exact=1, misaligns
    # the half-resolution chroma by a row, which bleeds thin lines' color into their neighbours.
    seek = ["-ss", f"{start:.3f}"] if start > 0 else []
    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", *seek, "-i", str(video), "-vf", f"format=rgb24,crop={info.width}:{rows}:0:{y0}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    size = rows * info.width * 3
    assert proc.stdout is not None
    try:
        while len(chunk := proc.stdout.read(size)) == size:
            yield np.frombuffer(chunk, np.uint8).reshape(rows, info.width, 3)
    finally:  # consumer may stop early
        proc.kill()
        proc.wait()


def colorfulness(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """HSV saturation and value in [0, 1]."""
    rgb = rgb.astype(np.float32) / 255
    mx, mn = rgb.max(-1), rgb.min(-1)
    return (mx - mn) / np.maximum(mx, 1e-6), mx


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of the True runs in a 1-D boolean array."""
    padded = np.r_[False, mask, False].astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2], edges[1::2]))


def find_hit_line(frame: np.ndarray) -> int:
    redness = (frame[..., 0].astype(int) - frame[..., 1:].mean(-1)).mean(axis=1)
    lower = frame.shape[0] // 2
    return lower + int(np.argmax(redness[lower:]))


def keyboard_band(frame: np.ndarray) -> tuple[int, int]:
    """[top, bottom) rows of the white keys: the longest run of rows whose *brightest* pixels are
    white (90th percentile). A row mean would dip where black keys and a pianist's hands cover
    most of it and cut the band in two."""
    rows = np.percentile(frame.mean(-1), 90, axis=1) > 100  # white keys ~120+ (dim), background ~45
    lower = frame.shape[0] // 3
    a, b = max(runs(rows[lower:]), key=lambda r: r[1] - r[0])
    return lower + int(a), lower + int(b)


WHITE_STEPS = (2, 1, 2, 2, 1, 2, 2)  # semitones from A, B, C, D, E, F, G to the next white key


def piano88_keys(idle: np.ndarray, band: tuple[int, int], video: Path, info: VideoInfo,
                 lit_rows: tuple[int, int], lit: Callable[[np.ndarray], np.ndarray]) -> list[Key]:
    """A full 88-key keyboard (A0..C8, 52 white keys) fitted to the visible black keys, then each
    key's column snapped onto the bars actually seen falling there.

    For keyboards partly hidden by a pianist's hands: the fit needs only the black keys left
    visible in the per-pixel median; real black keys sit a few px off the white-key boundaries,
    so the final columns come from where the bars are.
    """
    top, bottom = band
    lum = idle.mean(-1)
    black = np.array([(a + b - 1) / 2 for a, b in runs(lum[top + int(0.33 * (bottom - top))] < 80)
                      if 0.5 * idle.shape[1] / 52 < b - a < 0.95 * idle.shape[1] / 52])
    n_white = 52
    letters = "ABCDEFG"
    after = [i for i in range(n_white - 1) if letters[i % 7] in "ACDFG"]  # white keys with a black to the right

    def fit_error(x0: float, w: float) -> float:
        predicted = x0 + (np.array(after) + 1) * w
        return float(np.median(np.abs(black[:, None] - predicted[None, :]).min(axis=1)))

    w0 = idle.shape[1] / n_white
    _, x0, w = min((fit_error(x0, w), x0, w) for w in np.arange(0.95 * w0, 1.05 * w0, 0.02)
                   for x0 in np.arange(-0.5 * w0, 0.5 * w0, 0.25))
    pitch, keys = 21, []
    for i in range(n_white):
        keys.append(Key(x0 + (i + 0.5) * w, pitch, False))
        if i in after:
            keys.append(Key(x0 + (i + 1) * w, pitch + 1, True))
        pitch += WHITE_STEPS[i % 7]

    # Snap each key onto the centre of the bars falling on it (seen in ~80 frames).
    occupancy = np.zeros(idle.shape[1])
    for f in np.linspace(0.02 * info.n_frames, 0.98 * info.n_frames, 80, dtype=int):
        occupancy += lit(read_frame(video, info, f)[lit_rows[0]:lit_rows[1]]).sum(axis=0)
    peaks = [(a + b - 1) / 2 for a, b in runs(occupancy > 0.02 * occupancy.max())]
    # Each bar column belongs to its single nearest key: a white key and its black neighbour are
    # only half a key apart, and letting both snap to the same column read every note twice
    # (79% of notes got a semitone twin).
    xs = np.array([k.x for k in keys])
    column = {}
    for c in peaks:
        i = int(np.argmin(np.abs(xs - c)))
        if abs(xs[i] - c) < 0.35 * w and (i not in column or abs(c - xs[i]) < abs(column[i] - xs[i])):
            column[i] = c
    snapped = [Key(column.get(i, k.x), k.pitch, k.black) for i, k in enumerate(keys)]
    return [k for k in snapped if 0 <= k.x < idle.shape[1]]


def find_keys(frame: np.ndarray, hit_line: int) -> list[Key]:
    """Locate every key and name it from the black-key pattern. Frame must show an idle keyboard.

    Pitches assume the middle of the keyboard is near middle C; the true octave is fixed later
    by aligning against audio (`align.estimate_alignment` returns the needed shift).
    """
    h, w, _ = frame.shape
    lum = frame.mean(-1)
    keyboard = h - hit_line

    # White keys: at the bottom only white keys exist, separated by thin dark lines.
    seps = [(a + b - 1) / 2 for a, b in runs(lum[hit_line + int(0.85 * keyboard)] < 150)]
    bounds = np.array([0.0, *seps, float(w)])
    white_w = float(np.median(np.diff(bounds)))

    # Black keys: dark runs of plausible width in the upper part of the keyboard.
    black_runs = [
        (float(a), float(b))
        for a, b in runs(lum[hit_line + int(0.33 * keyboard)] < 80)
        if 0.4 * white_w < b - a < 0.9 * white_w
    ]
    # Which white-white boundary each black key sits on -> its [start, end) columns.
    black_after = {int(np.argmin(np.abs(bounds[1:-1] - (a + b - 1) / 2))): (a, b) for a, b in black_runs}

    # Sample white keys at the centre of their *visible upper part* (between the black
    # neighbours): black-key bars can never reach there. (White-key bars span the full key
    # width, so they DO cover black-key columns; `read_notes` separates those by colour.)
    white_x = []
    for i in range(len(bounds) - 1):
        left = max(bounds[i], black_after[i - 1][1]) if i - 1 in black_after else bounds[i]
        right = min(bounds[i + 1], black_after[i][0]) if i in black_after else bounds[i + 1]
        white_x.append((left + right) / 2)

    # Name the white keys: find the letter offset that best explains the black-key pattern.
    n_white = len(white_x)
    has_black = [j in black_after for j in range(n_white - 1)]
    offset = max(range(7), key=lambda o: sum(
        has_black[j] == HAS_BLACK_AFTER[(o + j) % 7] for j in range(n_white - 1)))

    # Choose the octave putting the middle white key closest to middle C (MIDI 60).
    def white_pitch(i: int, base_octave: int) -> int:
        letter, octave = (offset + i) % 7, base_octave + (offset + i) // 7
        return 12 * (octave + 1) + WHITE_SEMITONES[letter]

    base = min(range(-1, 8), key=lambda b: abs(white_pitch(n_white // 2, b) - 60))
    keys = [Key(float(x), white_pitch(i, base), False) for i, x in enumerate(white_x)]
    keys += [Key((a + b - 1) / 2, white_pitch(j, base) + 1, True) for j, (a, b) in black_after.items()]
    return sorted(keys, key=lambda k: k.pitch)


def calibrate(video: Path, info: VideoInfo, probes: int = 24, style: Style = STYLES["synthesia"]) -> tuple[int, np.ndarray]:
    """(hit line row, idle keyboard frame).

    Intros/outros use other layouts, so the hit line is the most common one across probes.
    No single frame is guaranteed idle, but any given key is unlit in most frames, so the
    per-pixel median over the matching probes is a clean, idle keyboard.
    """
    frames = [read_frame(video, info, i) for i in np.linspace(0, info.n_frames - 2, probes, dtype=int)]
    if style.hit_line == "keyboard":  # fades at both ends are black: use the frames that show the keys
        frames = [f for f in frames if f.mean() > 5]
        idle = np.median(np.stack(frames), axis=0).astype(np.uint8)
        return keyboard_band(idle)[0] - 1, idle
    lines = [find_hit_line(f) for f in frames]
    y = max(set(lines), key=lines.count)
    matching = [f for f, line in zip(frames, lines) if line == y]
    idle = matching[0].copy()
    idle[y:] = np.median(np.stack([f[y:] for f in matching]), axis=0).astype(np.uint8)
    return y, idle


def estimate_scroll(video: Path, info: VideoInfo, hit_line: int, gap: int = 10, n: int = 40) -> float:
    """Bar speed in px/frame: vertical shift between streamed frames `gap` apart (sub-pixel)."""
    buf = [b.mean(-1) for _, b in zip(range(n + gap), stream_band(
        video, info, 0, hit_line - 40, start=0.4 * info.n_frames / info.fps))]
    h = buf[0].shape[0]
    max_shift = min(h // 2, 40 * gap)
    speeds = []
    for a, b in zip(buf, buf[gap:]):
        if a.std() < 5:  # nothing on screen
            continue
        err = np.array([np.abs(a[: h - s] - b[s:]).mean() for s in range(max_shift)])
        s = int(np.argmin(err))
        if 0 < s < max_shift - 1:  # parabolic refinement around the minimum
            l, c, r = err[s - 1 : s + 2]
            s += 0.5 * (l - r) / (l - 2 * c + r)
        speeds.append(s / gap)
    return float(np.median(speeds))


@dataclass(frozen=True)
class Geometry:
    info: VideoInfo
    y_line: int  # row where bars hit the keyboard
    idle: np.ndarray  # idle keyboard frame
    keys: list[Key]
    v: float  # scroll speed, px/frame
    style: Style = STYLES["synthesia"]

    @property
    def px_to_s(self) -> float:
        """Seconds per tape pixel."""
        return 1.0 / (self.v * self.info.fps)


TOP_BAND = (60, 140)  # rows read by the "top" band style


def lit_mask(style: Style, rgb: np.ndarray, black: np.ndarray | None = None) -> np.ndarray:
    """Which samples show a note bar.

    synthesia: white-key notes are pastel (saturation: light blue ~0.28, light green ~0.71),
    black-key notes fully saturated (~1.0); grid lines, sparkles, background stay < ~0.07. A black
    key's column is also covered by its white neighbours' full-width bars, so black keys only
    count saturated pixels (`black`: per-key flags, broadcast over the last axis).
    bright_red: at the top of a PianoX video bars are red and at full brightness (96% of coloured
    pixels there, brightness 1.0); the particle effects are blue and dim (brightness ~0.44).
    """
    sat, val = colorfulness(rgb)
    if style.lit == "bright_red":
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        return (sat > 0.4) & (val > 0.7) & (r > g) & (r > b)
    min_sat = np.where(black, 0.75, 0.18) if black is not None else 0.18  # valley between 0.70 and 0.98
    return (sat > min_sat) & (val > 0.35)


def measure(video: Path, style: Style = STYLES["synthesia"]) -> Geometry:
    info = probe(video)
    y_line, idle = calibrate(video, info, style=style)
    if style.keys == "piano88":
        keys = piano88_keys(idle, keyboard_band(idle), video, info, TOP_BAND, lambda rgb: lit_mask(style, rgb))
    else:
        keys = find_keys(idle, y_line)
    return Geometry(info, y_line, idle, keys, estimate_scroll(video, info, y_line), style)


def stitch_tape(
    video: Path,
    geo: Geometry,
    sample: Callable[[np.ndarray], np.ndarray],
    margin_above_line: int = 60,
) -> np.ndarray:
    """Rebuild the scrolling tape: index u holds what crosses the hit line at u * geo.px_to_s.

    sample: maps a (rows, width, 3) band to per-row values of shape (rows, ...).
    margin_above_line: skip the rows right above the hit line, where the 'sparkle' effects live.
    Frames without the hit line (intro/outro cards) are skipped; their span stays zero.
    """
    info, y_line, v = geo.info, geo.y_line, geo.v
    rows = int(np.ceil(v)) + 8  # a little overlap between consecutive frames' bands
    y0 = TOP_BAND[0] if geo.style.band == "top" else y_line - margin_above_line - rows
    check = geo.style.hit_line == "red"  # other styles: dark fades simply show no bars

    def redness(row: np.ndarray) -> float:
        return float((row[..., 0].astype(int) - row[..., 1:].mean(-1)).mean())

    red_min = 0.5 * redness(geo.idle[y_line])
    length = int(np.ceil(info.n_frames * v)) + y_line + 1
    tape: np.ndarray | None = None
    count = np.zeros(length, np.float32)
    f, valid = -1, 0
    # Stream down to the hit line too, to check it is on screen.
    for f, band in enumerate(stream_band(video, info, y0, y_line - y0 + 2 if check else rows)):
        if check and max(redness(band[-3]), redness(band[-2]), redness(band[-1])) < red_min:
            continue
        valid += 1
        samples = np.asarray(sample(band[:rows]), np.float32)
        if tape is None:
            tape = np.zeros((length, *samples.shape[1:]), np.float32)
        base = round(f * v + y_line - y0)  # tape index of the band's top row
        lo = base - rows + 1
        if lo < 0:
            continue
        tape[lo : base + 1] += samples[::-1]  # bottom row = earliest tape position
        count[lo : base + 1] += 1
    if f + 1 != info.n_frames:
        raise RuntimeError(f"Streamed {f + 1} frames but the video has {info.n_frames}")
    if tape is None or (check and valid < 0.5 * info.n_frames):
        raise RuntimeError(f"Only {valid}/{info.n_frames} frames show the hit line; is this a Synthesia video?")
    return tape / np.maximum(count, 1).reshape(-1, *[1] * (tape.ndim - 1))


def read_notes(
    video: Path,
    geo: Geometry | None = None,
    colors: dict[str, Hand] | None = None,
    min_px: int = 4,
    gap_dip: float = 0.7,
) -> Transcription:
    """Extract every note (with its hand) from a Synthesia-style video.

    colors: which bar color is which hand, {"blue": "L", "green": "R"} by default.
    min_px: runs shorter than this on the tape are treated as noise.
    gap_dip: a bar is split where its brightness falls below this fraction of its median
        (the gap between two repeated notes).
    """
    colors = colors or {"blue": "L", "green": "R"}
    geo = geo or measure(video)
    keys = geo.keys
    cols = np.array([np.arange(round(k.x) - 2, round(k.x) + 3) for k in keys]).clip(0, geo.info.width - 1)
    tape = stitch_tape(video, geo, lambda band: band[:, cols].mean(axis=2))  # (length, keys, 3)

    lit = lit_mask(geo.style, tape, np.array([k.black for k in keys]))
    lit[1:-1] |= lit[:-2] & lit[2:]  # heal single-pixel compression holes
    _, val = colorfulness(tape)

    notes = []
    for k, key in enumerate(keys):
        for a, b in runs(lit[:, k]):
            # The few-px gap between repeated notes is blurred by compression and rarely drops
            # to background level, but it is always a clear dip relative to the bar itself.
            solid = val[a:b, k] > gap_dip * np.median(val[a:b, k])
            for sa, sb in runs(solid):
                sa, sb = a + sa, a + sb
                if sb - sa < min_px:
                    continue
                hand = None
                if geo.style.hands_by_color:
                    r, g, bl = tape[sa:sb, k].mean(axis=0)
                    hand = colors.get("blue" if bl > max(r, g) else "green" if g > max(r, bl) else "red")
                notes.append(NoteEvent(key.pitch, sa * geo.px_to_s, sb * geo.px_to_s, 80, hand))
    return Transcription(notes)


def read_bar_lines(video: Path, geo: Geometry | None = None, min_brightness: float = 4.0) -> np.ndarray:
    """Times (s) of the faint full-width bar lines that scroll with the notes (= downbeats).

    A bar line lifts the *median* brightness across the whole width (~10 vs 0), which note bars,
    covering only a few columns, never do. A faint line can split into two detections a few ms
    apart, so detections within a quarter bar are merged; lines hidden by compression are
    re-inserted where a gap is a whole multiple of the typical bar length.
    """
    geo = geo or measure(video)
    brightness = stitch_tape(video, geo, lambda band: np.median(band.max(-1), axis=1))
    raw = [(a + b) / 2 * geo.px_to_s for a, b in runs(brightness > min_brightness) if b - a < 30]
    if len(raw) < 3:
        return np.array(raw)
    bar = float(np.median(np.diff(raw)))  # duplicates are rare: the median is unaffected
    times = [raw[0]]
    for t in raw[1:]:
        if t - times[-1] > bar / 4:
            times.append(t)
    bar = float(np.median(np.diff(times)))
    filled = [times[0]]
    for t in times[1:]:
        n = round((t - filled[-1]) / bar)
        filled += [filled[-1] + (t - filled[-1]) * k / n for k in range(1, n)] if n > 1 else []
        filled.append(t)
    return np.array(filled)
