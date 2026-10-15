"""Offline vector icons. Main navigation uses real Flaticon Uicons SVG assets.

The eight attributed Uicons are generated at build time and bundled locally;
existing hand-drawn paths remain an offline fallback for source checkouts.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

NAV_ASSETS = Path(__file__).resolve().parent / 'assets' / 'ui_nav'
NAV_STEMS = {
    'Home': 'home',
    'Instructions': 'instructions',
    'Asset Library': 'asset-library',
    'Modding': 'modding',
    'Play & Restore': 'play-restore',
    'Settings': 'settings',
    'Credits': 'credits',
    "What's New": 'whats-new',
    'Developer': 'developer',
}
from PySide6.QtCore import QByteArray, Qt, QRectF
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "Home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "Instructions": '<path d="M12 7c-3-2-6-2-9-1v13c3-1 6-1 9 1m0-13c3-2 6-2 9-1v13c-3-1-6-1-9 1m0-13v13"/>',
    "Asset Library": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v11h14V9M9 14h6"/>',
    "Modding": '<path d="M14 7a5 5 0 0 0-6 6l-5 5a2 2 0 0 0 3 3l5-5a5 5 0 0 0 6-6l-3 3-3-3z"/>',
    "Play & Restore": '<path d="m8 5 10 7-10 7z"/><path d="M4 4v16"/>',
    "Settings": '<path d="M4 7h16M4 17h16M9 4v6M15 14v6"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>',
    "Credits": '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M17 5a3 3 0 0 1 0 6m0 4a5 5 0 0 1 4 5"/>',
    "What's New": '<path d="m12 2 2.1 7.9L22 12l-7.9 2.1L12 22l-2.1-7.9L2 12l7.9-2.1z"/>',
    'Developer': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M12 16h5"/>',
}

# Shared asset/action pictograms for the file explorers, texture workstation,
# GFX tools, and GPU controls. Shapes are bundled vector paths, rather than
# remote image URLs or OS-dependent emoji glyphs.
PATHS.update({
    "folder": '<path d="M3 6h7l2 3h9v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 10h18"/>',
    "file": '<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v6h5M9 12h7M9 16h7"/>',
    "text": '<path d="M4 5h16M12 5v15M8 20h8"/>',
    "texture": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m4 17 5-5 4 3 3-4 5 6"/>',
    "model": '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5m-9 5v10"/>',
    "gfx": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4m-4 6h4m10-6h4m-4 6h4m-7-7 5 4-5 4z"/>',
    "search": '<circle cx="10" cy="10" r="6"/><path d="m14.5 14.5 6 6"/>',
    "favorite": '<path d="m12 2 3.1 6.4 7 1-5 5 1.2 7-6.3-3.3-6.3 3.3 1.2-7-5-5 7-1z"/>',
    "open": '<path d="M14 4h6v6m0-6-9 9"/><path d="M19 14v5H5V5h5"/>',
    "save": '<path d="M4 3h14l3 3v15H3V3zM7 3v7h10V3M7 21v-8h10v8"/>',
    "import": '<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
    "export": '<path d="M12 21V8m-5 5 5-5 5 5M4 7V3h16v4"/>',
    "edit": '<path d="m4 20 5-1 11-11-4-4L5 15zM14 6l4 4M3 21h18"/>',
    "restore": '<path d="M4 9V3m0 6h6M4 9a9 9 0 1 1-1 7"/>',
    "refresh": '<path d="M20 7V3m0 4h-4M4 17v4m0-4h4M5 9a8 8 0 0 1 14-2M19 15a8 8 0 0 1-14 2"/>',
    "fit": '<path d="M8 3H3v5m0-5 7 7M16 3h5v5m0-5-7 7M3 16v5h5m-5 0 7-7m11 2v5h-5m5 0-7-7"/>',
    "view": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "material": '<path d="M12 2a10 10 0 1 0 0 20 4 4 0 0 0 3-7l-1-1h4a4 4 0 0 0 4-4 10 10 0 0 0-10-8z"/><circle cx="8" cy="7" r="1"/><circle cx="15" cy="7" r="1"/><circle cx="6" cy="13" r="1"/>',
    "normals": '<path d="m12 20 8-8-8-8-8 8zM12 4v16m-8-8h16"/>',
    "grid": '<rect x="3" y="3" width="18" height="18" rx="1"/><path d="M9 3v18M15 3v18M3 9h18M3 15h18"/>',
    "wire": '<path d="m12 2 9 5v10l-9 5-9-5V7zM3 7l9 5 9-5m-9 5v10M3 17l18-10M3 7l18 10"/>',
    "solid": '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18M3 12h18"/>',
    "animation": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m10 8 6 4-6 4zM3 8h4M3 16h4"/>',
    "play": '<path d="m7 3 14 9-14 9z"/>',
    "pause": '<rect x="5" y="4" width="5" height="16" rx="1"/><rect x="14" y="4" width="5" height="16" rx="1"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "undo": '<path d="M9 7 4 12l5 5M4 12h10a6 6 0 0 1 0 12"/>',
    "settings": '<circle cx="12" cy="12" r="4"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2"/>',
    "folder-open": '<path d="M3 7V5a2 2 0 0 1 2-2h6l2 3h6v4M3 11h19l-3 9H5z"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 11v6m0-10h.01"/>',
    "layers": '<path d="m12 2 10 6-10 6L2 8zM2 13l10 6 10-6M2 18l10 5 10-5"/>',
    "zoom": '<circle cx="10" cy="10" r="6"/><path d="M10 7v6m-3-3h6m1.5 4.5 6 6"/>',
    "external": '<path d="M14 4h6v6m0-6-9 9M19 14v5H5V5h5"/>',
    "compare": '<rect x="2" y="4" width="9" height="16" rx="1"/><rect x="13" y="4" width="9" height="16" rx="1"/>',
    "delete": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v7m4-7v7"/>',
})


# Do not change protected-page lifecycle or expose it through icon metadata.


@lru_cache(maxsize=128)
def navigation_icon(section, color="#f5f3f6", size=24):
    drawing = PATHS.get(section)
    if drawing is None:
        return QIcon()
    safe_color = color if len(color) == 7 and color.startswith("#") else "#f5f3f6"
    asset = NAV_ASSETS / (NAV_STEMS[section] + ".svg") if section in NAV_STEMS else None
    if asset is not None and asset.is_file():
        # Resolved entirely from the local bundled app. Do not load URLs.
        svg = asset.read_text(encoding="utf-8").replace("#f5f3f6", safe_color)
    else:
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
           'viewBox="0 0 24 24" fill="none" stroke="' + safe_color +
           '" stroke-width="1.8" stroke-linecap="round" '
           'stroke-linejoin="round">' + drawing + '</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise ValueError("Invalid bundled navigation SVG")
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(2)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # Explicit logical painting bounds prevent high-DPI Qt from clipping a
    # source SVG to its intrinsic width/height instead of the icon's 18–24px
    # surface. A little uniform padding leaves clear button/label spacing.
    inset = max(1.0, size * 0.07)
    renderer.render(painter, QRectF(inset, inset, size - 2 * inset,
                                  size - 2 * inset))
    painter.end()
    return QIcon(pixmap)


def action_icon(label, color="#f5f3f6", size=18):
    """Return a consistent icon for a visible UI action without text changes."""
    value = label.strip().casefold().replace("…", "").replace("...", "")
    mapping = (
        (("save",), "save"),
        (("import", "add ", "load ", "choose "), "import"),
        (("export",), "export"),
        (("restore", "undo", "revert"), "restore"),
        (("replace", "edit "), "edit"),
        (("refresh", "reload"), "refresh"),
        (("find ", "search"), "search"),
        (("open folder", "explorer", "reveal"), "folder-open"),
        (("favorite",), "favorite"),
        (("texture", "dds", "png"), "texture"),
        (("gfx", "scaleform", "ffdec"), "gfx"),
        (("model", "mesh"), "model"),
        (("play",), "play"),
        (("fit",), "fit"),
        (("grid",), "grid"),
        (("wire",), "wire"),
        (("material",), "material"),
        (("info", "details"), "info"),
    )
    for words, key in mapping:
        if any(word in value for word in words):
            return navigation_icon(key, color, size)
    return QIcon()
