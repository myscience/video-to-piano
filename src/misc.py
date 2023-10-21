import numpy as np

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
