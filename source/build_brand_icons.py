"""Generate correctly sized red/black Windows ICO frames from the tracked PNG.

Use the single supplied artwork as the source of truth. The 16/32/48/256 ICO
is regenerated before branding tests and PyInstaller packaging, preventing
Windows from retaining the obsolete blue-tinged application icon.
"""
from __future__ import annotations
from pathlib import Path

from PIL import Image


def rebuild_icons(source=None, destination=None):
    root = Path(__file__).resolve().parent
    source = Path(source) if source else root / 'assets' / 'hunt.png'
    destination = Path(destination) if destination else root / 'assets' / 'hunt.ico'
    with Image.open(source) as loaded:
        image = loaded.convert('RGBA')
    if image.width != image.height or image.width < 256:
        raise ValueError('App logo must be a square PNG at least 256x256.')
    # 256x256 PNG and three optimized icon sizes; intentionally opaque because
    # the supplied artwork has a red field rather than transparent corners.
    image.save(destination, format='ICO',
               sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    with Image.open(destination) as verify:
        if not {(16, 16), (32, 32), (48, 48), (256, 256)}.issubset(verify.ico.sizes()):
            raise ValueError('Incomplete ICO sizes after generation.')
    print('Red/black Evolve application icon ready:', destination, flush=True)
    return destination


if __name__ == '__main__':
    rebuild_icons()
