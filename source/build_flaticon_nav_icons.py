"""Build eight nav SVGs from Flaticon's official Uicons regular-rounded font.

The input is pinned to Flaticon's public GitHub repo, not scraped from the
Flaticon website. Only eight SVG paths are emitted; the full webfont is never
packaged with the manager. See Docs/FLATICON-UICONS-ATTRIBUTION.txt.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

# Official project: https://github.com/freepik-company/flaticon-uicons
UPSTREAM_COMMIT = "01c7cf8e403afdf879ff56c99235a34df81db6a2"
UPSTREAM_PATH = "src/uicons/webfonts/uicons-regular-rounded.woff"
EXPECTED_GIT_BLOB = "71f829a7fec21786dfabafd67b32dd62ccf04827"
SOURCE_URL = (
    "https://raw.githubusercontent.com/freepik-company/flaticon-uicons/"
    + UPSTREAM_COMMIT + "/" + UPSTREAM_PATH
)
MAX_SOURCE_BYTES = 700_000
# Codes from official src/uicons/css/regular/rounded.css at the same commit.
NAV_GLYPHS = {
    "instructions": ("book-alt", 0xF274),
    "asset-library": ("archive", 0xF16D),
    "modding": ("tools", 0xFDCB),
    "play-restore": ("play", 0xFA9E),
    "settings": ("settings", 0xFBD2),
    "credits": ("users", 0xFEA6),
    "whats-new": ("newspaper", 0xF9A5),
    "developer": ("terminal", 0xFD78),
}


def read_pinned_upstream():
    request = Request(SOURCE_URL, headers={"User-Agent": "EvolveModManager-Build"})
    with urlopen(request, timeout=30) as response:
        source = response.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("Uicons source webfont exceeds the pinned size limit")
    actual = hashlib.sha1(
        b"blob " + str(len(source)).encode("ascii") + b"\0" + source
    ).hexdigest()
    if actual != EXPECTED_GIT_BLOB:
        raise ValueError("Flaticon Uicons source digest mismatch: " + actual)
    return source


def generate_nav_icons(target, source=None):
    """Convert only the eight matching source glyph outlines to clean SVGs."""
    from fontTools.ttLib import TTFont
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen

    data = read_pinned_upstream() if source is None else source
    font = TTFont(BytesIO(data))
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    dest = Path(target)
    dest.mkdir(parents=True, exist_ok=True)
    completed = []
    for file_stem, (glyph_title, codepoint) in NAV_GLYPHS.items():
        name = cmap.get(codepoint)
        if not name or name not in glyphs:
            raise ValueError(f"Missing Flaticon Uicons glyph {glyph_title} ({codepoint:#x})")
        glyph = glyphs[name]
        bounds = BoundsPen(glyphs)
        glyph.draw(bounds)
        if bounds.bounds is None:
            raise ValueError("Empty Flaticon icon: " + glyph_title)
        x0, y0, x1, y1 = bounds.bounds
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Invalid Flaticon glyph bounds: " + glyph_title)
        # Font units can have thousands of units of advance-space and unusual
        # bounds. Convert every actual glyph outline into the SAME centered
        # 24x24 pixel coordinate space; do not rely on QtSvg interpreting a
        # dynamically sized font-unit viewBox on a high-DPI 18px pixmap.
        width, height = x1 - x0, y1 - y0
        padding = 1.5
        scale = (24 - 2 * padding) / max(width, height)
        xpadding = (24 - width * scale) / 2
        ypadding = (24 - height * scale) / 2
        pen = SVGPathPen(glyphs)
        normalized = TransformPen(
            pen, (scale, 0, 0, -scale,
                  xpadding - x0 * scale, ypadding + y1 * scale))
        glyph.draw(normalized)
        drawing = pen.getCommands()
        if not drawing:
            raise ValueError("Missing Flaticon path data: " + glyph_title)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="24" height="24" viewBox="0 0 24 24" '
            'preserveAspectRatio="xMidYMid meet">'
            f'<path fill="#f5f3f6" d="{drawing}"/></svg>\n'
        )
        file = dest / (file_stem + ".svg")
        file.write_text(svg, encoding="utf-8")
        completed.append(file)
    font.close()
    print("Generated %d Flaticon Uicons navigation SVGs" % len(completed))
    return completed


if __name__ == "__main__":
    generate_nav_icons(Path(__file__).resolve().parent / "assets" / "ui_nav")
