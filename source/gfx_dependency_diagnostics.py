"""Evidence-based same-build Scaleform dependencies shown by the GFX inspector.

This reports only verified imported movies, explicitly linked DDS resources,
UI_Data associations, embedded fonts and native videos. It does not promise
that the game would draw an asset without its ActionScript and localization.
"""
from __future__ import annotations

from dataclasses import dataclass

from gfx_preview_backend import inspect_gfx, safe_import_path, GfxError
from gfx_video_preview import video_streams
from gfx_placement_diagnostics import _root_tags


@dataclass(frozen=True)
class Dependency:
    kind: str
    name: str
    origin: str
    status: str


def dependency_report(data, virtual, *, resolved_imports=None, imported_issues=(),
                      texture_paths=(), bitmap_bindings=(), available_paths=()):
    """Give the exact origin and state of every verified static dependency.

    Movie paths are already resolved by resolve_imports() with the caller's
    strict same-build resolver. Do not walk unrelated asset-library builds.
    """
    info = inspect_gfx(data)
    resolved_imports = resolved_imports or {}
    available = {str(name).casefold(): str(name) for name in available_paths}
    rows = []
    for url in info.imports:
        try:
            requested = safe_import_path(url, virtual)
        except (ValueError, GfxError):
            rows.append(Dependency("Imported movie", str(url)[:180],
                                   "ImportAssets2", "Unsafe path (not loaded)"))
            continue
        actual = requested
        if requested.casefold().endswith(".swf") and (
                requested.casefold() not in available):
            compiled = requested[:-4] + ".gfx"
            if compiled.casefold() in available:
                actual = available[compiled.casefold()]
        kind = ("Font / movie" if "font" in url.casefold()
                else "Imported movie")
        ok = requested in resolved_imports
        rows.append(Dependency(
            kind, requested, actual if ok else "Not resolved in selected build",
            "Resolved (compiled GFX)" if ok and actual != requested
            else "Resolved" if ok else "Missing / ambiguous"))

    seen = set()
    for path, origin in texture_paths:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(Dependency("DDS texture", str(path), str(origin),
                               "Found in selected build"))

    for bitmap in bitmap_bindings:
        path = str(bitmap.texture)
        if path.casefold() in seen:
            continue
        seen.add(path.casefold())
        rows.append(Dependency(
            "DDS bitmap #" + str(bitmap.bitmap_character), path,
            "Scaleform 1008/1009 image atlas",
            "Found in selected build" if bitmap.file_found
            else "Missing / ambiguous"))

    embedded_fonts = set()
    for code, payload in _root_tags(data):
        if code in (10, 48, 75, 91) and len(payload) >= 2:
            embedded_fonts.add(int.from_bytes(payload[:2], "little"))
    for cid in sorted(embedded_fonts):
        rows.append(Dependency("Font", "#" + str(cid),
                               "Embedded font definition", "Available statically"))

    for stream in video_streams(data):
        rows.append(Dependency(
            "Video", "#" + str(stream.character_id),
            f"Embedded {stream.width}x{stream.height}, {stream.frames} frames",
            "Playback unavailable; placeholder hidden in preview"))

    for reason in imported_issues:
        if not any(str(reason) in row.status for row in rows):
            rows.append(Dependency("Import warning", str(reason)[:180],
                                   "GFX ImportAssets2", "Not rendered"))

    if info.script:
        rows.append(Dependency("ActionScript", "Runtime UI",
                               "Embedded Scaleform scripts",
                               "Not executed by static FFDec frame export"))
    return tuple(rows[:640])
