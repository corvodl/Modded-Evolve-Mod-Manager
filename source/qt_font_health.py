"""Resolve actual, readable Windows fonts for both Qt and frozen screenshots.

Qt's offscreen plugin can start on Windows with no usable font fallback. In
that state labels render as identical square/tofu glyphs while UI/screenshot
smoke tests falsely pass. Register system TrueType files explicitly; never
ship or copy proprietary Windows fonts into the portable manager.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetrics, QImage, QPainter,
)

_FONT_FAMILY = 'Segoe UI'


def ui_family():
    return _FONT_FAMILY


def glyph_signature(font: QFont, char: str):
    """Render one character with the same Qt font engine as the GUI."""
    image = QImage(82, 84, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(QColor('#ffffff'))
        painter.setFont(font)
        painter.drawText(QRect(2, 2, 78, 80), Qt.AlignCenter, char)
    finally:
        painter.end()
    ink = sum(image.pixelColor(x, y).alpha() > 40
              for y in range(3, 81, 2)
              for x in range(3, 79, 2))
    return bytes(image.bits()), ink


def glyphs_readable(font: QFont) -> bool:
    """Detect missing-font/tofu rendering without claiming to read text."""
    metrics = QFontMetrics(font)
    if not all(metrics.inFontUcs4(ord(ch)) for ch in 'ABCMg29'):
        return False
    samples = [glyph_signature(font, ch) for ch in 'ABMg29']
    # The tofu glyph is typically the identical rectangle for every
    # character; actual ASCII glyphs have distinct rasterized shapes.
    return (all(ink >= 6 for _, ink in samples) and
            len({signature for signature, _ in samples}) == len(samples))


def ensure_readable_ui_font(app, *, strict=False) -> str:
    """Set a readable Qt font, fail frozen screenshot capture if impossible.

    First explicitly register existing Windows UI fonts so the offscreen Qt
    renderer can use them even when the platform font database is empty.
    Qt on other platforms uses installed system fonts only.
    """
    global _FONT_FAMILY
    candidates = []
    if os.name == 'nt':
        fonts = Path(os.environ.get('WINDIR') or r'C:\Windows') / 'Fonts'
        for filename in ('segoeui.ttf', 'arial.ttf', 'tahoma.ttf',
                         'verdana.ttf', 'calibri.ttf'):
            path = fonts / filename
            if not path.is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                candidates.extend(QFontDatabase.applicationFontFamilies(font_id))
    candidates.extend(('Segoe UI', 'Arial', 'Tahoma', 'Verdana',
                       'Noto Sans', 'DejaVu Sans', 'Liberation Sans'))
    try:
        candidates.append(QFontDatabase.systemFont(QFontDatabase.GeneralFont).family())
    except Exception:
        pass
    available = set(QFontDatabase.families())
    for family in dict.fromkeys(candidates):
        if family not in available:
            continue
        font = QFont(family, 10)
        font.setStyleStrategy(QFont.PreferAntialias)
        if glyphs_readable(font):
            _FONT_FAMILY = family
            app.setFont(font)
            return family
    if strict:
        raise RuntimeError(
            'No usable Qt text font was detected. Refusing to publish a '
            'compiled screenshot consisting of square placeholder glyphs.')
    # Non-screenshot runs keep a system default rather than forcing absent
    # Segoe UI onto machines where it does not exist.
    font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    _FONT_FAMILY = font.family()
    app.setFont(font)
    return _FONT_FAMILY
