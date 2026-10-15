"""Serialize decoded material images for OpenGL with source UV orientation.

CryEngine mesh UVs, the CPU renderer, and GLB export all sample decoded
image row zero at v=0. OpenGL likewise assigns the first uploaded byte row
to v=0, so uploading source rows verbatim preserves texture landmarks.

Pillow's top/bottom terminology describes image display coordinates, not
the engine's UV convention. Do not introduce a compensating flip here.
"""
from __future__ import annotations

from PIL import Image


def gl_upload_pixels(source, mode='RGB'):
    if mode not in ('RGB', 'RGBA'):
        raise ValueError('Only RGB/RGBA material uploads are supported.')
    sample = source.convert(mode)
    if sample.width < 1 or sample.height < 1:
        raise ValueError('Invalid source image dimensions.')
    return sample.tobytes()
