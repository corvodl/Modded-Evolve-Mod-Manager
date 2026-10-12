"""Match OpenGL texture memory to decoded CryEngine/Pillow UV coordinates.

The CPU material preview treats v=0 as the first/top row of a decoded DDS.
OpenGL glTexImage2D interprets the first supplied row as v=0/bottom.
Reverse only the upload memory, never the original DDS or the material UVs.
"""
from __future__ import annotations

from PIL import Image


def gl_upload_pixels(source, mode='RGB'):
    if mode not in ('RGB', 'RGBA'):
        raise ValueError('Only RGB/RGBA material uploads are supported.')
    sample = source.convert(mode)
    if sample.width < 1 or sample.height < 1:
        raise ValueError('Invalid source image dimensions.')
    return sample.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes()
