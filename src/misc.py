import numpy as np
from abjad import Note
from typing import Dict, List, Callable, Any

from collections import defaultdict

def default(var : Any, val : Any) -> Any:
    return val if var is None else var

def lazydefault(expr : Callable, err : Any = None) -> Any:
    try:
        out = expr()
    except Exception:
        out = err
    
    return out

def mse(a : np.ndarray, b : np.ndarray, axis : int | None = None) -> float | np.ndarray:
    a = np.array(a)
    b = np.array(b)

    return np.square(a - b).mean(axis = axis)

# Credit to: https://stackoverflow.com/questions/8914491/finding-the-nearest-value-and-return-the-index-of-array-in-python
def find_closest_idx(
    array : np.ndarray,
    target : float,
    sorted : bool = True
) -> int:
    if not sorted: array = np.sort(array)

    idx = np.searchsorted(array, target)
    idx = np.clip(idx, 1, len(array) - 1)
    l, r = array[idx-1], array[idx]
    idx -= target - l < r - target

    return idx

def invert_dict(orig : Dict, exclude : List[str] = []) -> Dict:
    inv = defaultdict(list)

    for k, v in orig.items():
        if exclude in k: continue
        inv[v].append(k)

    return inv

def get_note_name(note : Note, drop_duration : bool = True) -> str:
    if drop_duration: return note.written_pitch.name

    name_1 = str(note).split("'")
    name_2 = str(note).split('"')

    name = name_1[1] if len(name_1) > len(name_2) else name_2[1]

    return name
