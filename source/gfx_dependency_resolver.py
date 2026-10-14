"""Scoped Scaleform ImportAssets fallback lookup.

CryEngine can store SWF/GFX dependencies in Libs/UI/FlashAssets rather than
the sibling name requested in the movie. Resolve the exact requested path
first, then a single same-build, same-UI-tree alternative. Do not take the
first arbitrary basename from unrelated PAKs or another game build.

DDS/streamed image assets are NOT stand-ins for actual .swf/.gfx imports.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from gfx_preview_backend import GfxError, _virtual_path

MOVIE_SUFFIXES = (".gfx", ".swf")


def choose_movie_provider(expected, source_virtual, providers):
    """Return (provider, source-description) or (None, diagnostic).

    providers: iterable of (virtual_path, arbitrary provider value). An exact
    duplicate is ambiguous even if a fallback exists; only one unambiguous
    available movie may satisfy the import.
    """
    wanted = _virtual_path(expected)
    source = _virtual_path(source_virtual)
    if not wanted.casefold().endswith(MOVIE_SUFFIXES):
        raise GfxError("A GFX import must refer to a movie, not a DDS image.")
    parent = PurePosixPath(source).parent.as_posix().casefold()
    if parent == ".":
        parent = ""
    requested_parent = PurePosixPath(wanted).parent.as_posix().casefold()
    # A direct child import is only allowed within the same namespace as the
    # source. Nested imports are handled by the strict virtual-path checker.
    root = (parent + "/") if parent else ""
    if requested_parent != parent and not (root and requested_parent.startswith(root)):
        raise GfxError("GFX dependency escapes the current UI folder.")
    by_exact, by_nested = [], []
    for relative, provider in providers:
        try:
            virtual = _virtual_path(relative)
        except GfxError:
            continue
        folded = virtual.casefold()
        if not folded.endswith(MOVIE_SUFFIXES):
            continue
        if folded == wanted.casefold():
            by_exact.append((virtual, provider))
        elif (folded.endswith("/" + PurePosixPath(wanted).name.casefold()) and
              (folded.startswith(root) if root else "/" not in folded)):
            by_nested.append((virtual, provider))
    if len(by_exact) == 1:
        return by_exact[0][1], "exact:" + by_exact[0][0]
    if len(by_exact) > 1:
        return None, "ambiguous exact ImportAssets providers: " + wanted
    # No implicit nearest/random match if multiple folders supply the movie.
    if len(by_nested) == 1:
        return by_nested[0][1], "same-UI-tree:" + by_nested[0][0]
    if len(by_nested) > 1:
        return None, "ambiguous nested ImportAssets providers: " + wanted
    return None, "missing actual .gfx/.swf dependency: " + wanted


def verified_movie_content(path, max_bytes):
    """Read only an existing, regular imported asset within its scoped root.

    The caller must build provider lists from manifest-listed Asset Library
    entries or checked workspace files; this does not walk the host filesystem.
    """
    from pathlib import Path
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > max_bytes:
        return None
    return file.read_bytes()
