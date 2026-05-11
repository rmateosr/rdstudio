"""Background color parsing — shared by both engines and the GUI."""

import numpy as np


NAMED_BG_COLORS = {
    "white": (255.0, 255.0, 255.0),
    "gray":  (128.0, 128.0, 128.0),
    "black": (0.0, 0.0, 0.0),
}


def parse_bg(bg):
    """Return an RGB (3,) float array for a background specifier.

    Accepts a named color ("white"/"gray"/"black"), an "#RRGGBB" hex string,
    or an already-resolved (r, g, b) tuple/array. Values are in [0, 255].
    """
    if isinstance(bg, (tuple, list, np.ndarray)):
        arr = np.asarray(bg, dtype=np.float64)
        if arr.shape != (3,):
            raise ValueError(f"bg tuple must have 3 components, got {arr.shape}")
        return arr
    if not isinstance(bg, str):
        raise TypeError(f"bg must be a str or 3-tuple, got {type(bg).__name__}")
    s = bg.strip()
    if s in NAMED_BG_COLORS:
        return np.asarray(NAMED_BG_COLORS[s], dtype=np.float64)
    if s.startswith("#") and len(s) == 7:
        try:
            r = int(s[1:3], 16)
            g = int(s[3:5], 16)
            b = int(s[5:7], 16)
            return np.asarray((r, g, b), dtype=np.float64)
        except ValueError:
            pass
    raise ValueError(f"Invalid bg: {bg!r} (use white/gray/black or #RRGGBB)")
