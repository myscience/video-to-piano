"""Piano-roll diagnostics: where does each backend disagree with the reference?"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mir_eval  # noqa: E402
import pretty_midi  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from ..transcribe.base import Transcription  # noqa: E402

SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
# Outcome = categorical slots 1-3 (validated all-pairs), each with its own secondary encoding.
MATCHED, MISSED, EXTRA = "#2a78d6", "#eb6834", "#1baf7a"


def _window(t: Transcription, start: float, end: float) -> Transcription:
    return Transcription([n for n in t.notes if n.offset > start and n.onset < end])


def piano_roll(
    ref: Transcription,
    ests: dict[str, Transcription],
    out: Path,
    start: float = 60.0,
    length: float = 8.0,
    onset_tolerance: float = 0.05,
) -> None:
    end = start + length
    ref_w = _window(ref, start, end)
    pitches = [n.pitch for n in ref_w.notes] + [n.pitch for e in ests.values() for n in _window(e, start, end).notes]
    lo, hi = min(pitches) - 2, max(pitches) + 2

    fig, axes = plt.subplots(len(ests), 1, figsize=(14, 3.2 * len(ests)), sharex=True, sharey=True,
                             facecolor=SURFACE, squeeze=False)
    for ax, (name, est) in zip(axes[:, 0], ests.items()):
        est_w = _window(est, start, end)
        ri, rp = ref_w.intervals_and_pitches()
        ei, ep = est_w.intervals_and_pitches()
        pairs = mir_eval.transcription.match_notes(ri, rp, ei, ep, onset_tolerance=onset_tolerance, offset_ratio=None)
        hit_ref, hit_est = {r for r, _ in pairs}, {e for _, e in pairs}

        for i, n in enumerate(ref_w.notes):
            ok = i in hit_ref
            ax.add_patch(Rectangle((n.onset, n.pitch - 0.4), n.duration, 0.8, linewidth=0,
                                   facecolor=MATCHED if ok else MISSED, alpha=0.85 if ok else 1.0,
                                   hatch=None if ok else "////", edgecolor=SURFACE))
        for j, n in enumerate(est_w.notes):
            if j not in hit_est:
                ax.add_patch(Rectangle((n.onset, n.pitch - 0.4), n.duration, 0.8, fill=False,
                                       edgecolor=EXTRA, linewidth=1.6))

        n_miss, n_extra = len(ref_w.notes) - len(hit_ref), len(est_w.notes) - len(hit_est)
        ax.set_title(f"{name}: {len(hit_ref)} matched · {n_miss} missed · {n_extra} extra",
                     loc="left", color=INK, fontsize=11)
        ax.set_facecolor(SURFACE)
        ax.set_xlim(start, end)
        ax.set_ylim(lo, hi)
        cs = [p for p in range(lo, hi + 1) if p % 12 == 0]
        ax.set_yticks(cs, [pretty_midi.note_number_to_name(p) for p in cs])
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=INK_2, labelsize=9)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)

    axes[-1, 0].set_xlabel("time in audio (s)", color=INK_2)
    fig.legend(
        handles=[Patch(facecolor=MATCHED, alpha=0.85, label="reference note, found"),
                 Patch(facecolor=MISSED, hatch="////", edgecolor=SURFACE, label="reference note, missed"),
                 Patch(fill=False, edgecolor=EXTRA, linewidth=1.6, label="extra note (not in reference)")],
        loc="upper right", ncol=3, frameon=False, labelcolor=INK, fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130, facecolor=SURFACE)
    plt.close(fig)
