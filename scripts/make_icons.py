"""Generate assets/icon.{png,ico,icns} from a single source PNG.

PyInstaller picks the right one per host OS (see rdstudio.spec). The Tk
runtime icon also reads assets/icon.png.

Usage:
    python scripts/make_icons.py path/to/source.png
"""

import os
import sys

from PIL import Image

ICO_SIZES = [(s, s) for s in (16, 32, 48, 64, 128, 256)]

# Pad color when the source isn't already square. Matches the f8f8f8
# background the cute icon was authored on; override with --bg.
DEFAULT_BG = (0xF8, 0xF8, 0xF8, 255)


def main(src, bg=DEFAULT_BG):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(here, "assets")
    os.makedirs(assets, exist_ok=True)

    im = Image.open(src).convert("RGBA")
    w, h = im.size
    # Pad to square so the figure is preserved intact, then resize.
    side = max(w, h)
    if (w, h) != (side, side):
        canvas = Image.new("RGBA", (side, side), bg)
        canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
        im = canvas
    im = im.resize((1024, 1024), Image.LANCZOS)

    png_path = os.path.join(assets, "icon.png")
    ico_path = os.path.join(assets, "icon.ico")
    icns_path = os.path.join(assets, "icon.icns")

    im.save(png_path, optimize=True)
    im.save(ico_path, sizes=ICO_SIZES)
    im.save(icns_path)

    for p in (png_path, ico_path, icns_path):
        print(f"  {os.path.relpath(p, here)}  ({os.path.getsize(p) // 1024} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
