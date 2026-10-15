"""Never substitute a different animation for a missing standalone CAF.

An actual DBA converter may emit multiple GLBs/clip names. Resolve only an
exactly named motion; leave ambiguous/missing tracks explicitly unavailable.
"""
from __future__ import annotations

from pathlib import Path
import re


def clip_key(name):
    # A filename extension is not part of the CryEngine motion label.
    return re.sub(r'\.(?:caf|glb)$', '', str(name).replace('\\', '/'), flags=re.I).casefold()


def converted_motion(folder, requested, *, parser=None):
    """Return (path, internal_clip_name) or raise if no exact match exists."""
    root = Path(folder).resolve()
    files = sorted(
        (p for p in root.rglob('*.glb')
         if p.is_file() and not p.is_symlink()
         and p.resolve().is_relative_to(root)),
        key=lambda item: item.as_posix().casefold())
    if len(files) > 512:
        raise ValueError('Converter produced too many GLBs to search safely.')
    from model_animation import AnimatedGLB
    parser = parser or AnimatedGLB.from_file
    goal = clip_key(requested)
    matches = []
    for file in files:
        # Names can be stored inside a multi-clip GLB rather than in its
        # filename, so verify the actual animation list before playing.
        try:
            converted = parser(file)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        for clip in converted.clips:
            key = clip_key(clip)
            # Exporters may retain the virtual path or only the asset basename.
            # Duplicate basenames still fail the unique-match check below.
            if key == goal or key.rsplit('/', 1)[-1] == goal.rsplit('/', 1)[-1]:
                matches.append((file, clip))
    if len(matches) != 1:
        raise ValueError(
            'Requested motion %r has %d exact match(es) in the converted DBA. '
            'No unrelated animation was substituted.' % (requested, len(matches)))
    return matches[0]
