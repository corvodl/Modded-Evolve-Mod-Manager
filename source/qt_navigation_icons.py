"""Offline vector navigation icons rendered through QtSvg at device pixel ratio.

Original path drawings (book, archive, tools, play, settings, people, history).
No icon fonts, emoji, online fetches, or external files at runtime.
"""
from __future__ import annotations

from functools import lru_cache
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

PATHS = {
    "Instructions": '<path d="M12 7c-3-2-6-2-9-1v13c3-1 6-1 9 1m0-13c3-2 6-2 9-1v13c-3-1-6-1-9 1m0-13v13"/>',
    "Asset Library": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v11h14V9M9 14h6"/>',
    "Modding": '<path d="M14 7a5 5 0 0 0-6 6l-5 5a2 2 0 0 0 3 3l5-5a5 5 0 0 0 6-6l-3 3-3-3z"/>',
    "Play & Restore": '<path d="m8 5 10 7-10 7z"/><path d="M4 4v16"/>',
    "Settings": '<path d="M4 7h16M4 17h16M9 4v6M15 14v6"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>',
    "Credits": '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M17 5a3 3 0 0 1 0 6m0 4a5 5 0 0 1 4 5"/>',
    "What's New": '<path d="m12 2 2.1 7.9L22 12l-7.9 2.1L12 22l-2.1-7.9L2 12l7.9-2.1z"/>',
}
# Do not change protected-page lifecycle or expose it through icon metadata.


@lru_cache(maxsize=128)
def navigation_icon(section, color="#f5f3f6", size=24):
    drawing = PATHS.get(section)
    if drawing is None:
        return QIcon()
    safe_color = color if len(color) == 7 and color.startswith("#") else "#f5f3f6"
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
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
