"""Read-only, same-build UI_Data associations for Scaleform texture previews.

CryEngine keeps compiled GFX and its external _I<hex>.dds(.0) images in
UI_Assets. UI_Data records the UIElement movie and preloaded DDS patterns.
These references do not specify a complete runtime display list: they must
not be silently overlaid on FFDec frames at guessed positions.
"""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree

from gfx_preview_backend import _virtual_path, safe_import_path

_IMAGE_SUFFIX = (".dds", ".dds.0")
_COMPANION = re.compile(r"_i[0-9a-f]+\.dds(?:\.0)?$", re.I)
# Scaleform tag 1009 contains source .tif names while cooked assets
# are .dds/.dds.0. A GFX may also embed literal DDS paths in scripts.
_GFX_REF = re.compile(
    rb"(?:Libs/UI/[A-Za-z0-9_./-]+|[A-Za-z0-9_-]+)\\.(?:dds|tif)(?:\\.0)?",
    re.I,
)
_MAX_UI_XML = 512 * 1024
_MAX_TEXTURES = 512


def related_ui_textures(movie, available_paths, resolver, *, imports=(), movie_bytes=b''):
    """Return (virtual DDS path, origin) for verified same-build resources.

    Paths are from the selected project's manifest or the explicitly selected
    build index, never an unscoped file-system crawl. The resolver is invoked
    for at most a few selected UI_Data XML documents; image bytes are loaded
    only when the user selects a texture in the viewer.
    """
    movie = _virtual_path(movie)
    base = PurePosixPath(movie)
    entries = {}
    for path in available_paths:
        try:
            clean = _virtual_path(path)
        except ValueError:
            continue
        if clean.casefold().endswith(_IMAGE_SUFFIX):
            entries.setdefault(clean.casefold(), clean)
    if not entries:
        return ()

    result = {}

    def add(path, origin):
        key = str(path).casefold()
        if key not in entries:
            return
        if key not in result and len(result) < _MAX_TEXTURES:
            result[key] = (entries[key], origin)
        elif key in result and origin == "GFX resource reference":
            # A literal compiled-movie reference is stronger evidence
            # than matching an _I<hex> companion filename alone.
            result[key] = (entries[key], origin)

    # Texture symbol IDs come from compiled GFX resources; don't match
    # arbitrary images with coincidental basenames.
    stems = [(str(base.with_suffix("")), "Compiled movie image")]
    for url in imports[:128]:
        try:
            imported = safe_import_path(url, movie)
            stems.append((str(PurePosixPath(imported).with_suffix("")),
                          "Imported movie image"))
        except ValueError:
            continue
    for stem, origin in stems:
        prefix = (stem + "_i").casefold()
        for key, path in entries.items():
            if key.startswith(prefix) and _COMPANION.search(key):
                add(path, origin)

    # Only trust strings present in the selected compiled movie. CryEngine
    # converts tag 1009's .tif names to packed .dds(.0) companions; the
    # XML preload list alone cannot establish image-to-symbol placement.
    if isinstance(movie_bytes, bytes):
        for match in _GFX_REF.findall(movie_bytes):
            try:
                raw = match.decode("ascii")
                virtual = raw if raw.casefold().startswith("libs/ui/") else str(
                    base.parent / raw)
                virtual = _virtual_path(virtual)
                if virtual.casefold().endswith(".tif"):
                    candidates = (virtual[:-4] + ".dds", virtual[:-4] + ".dds.0")
                elif virtual.casefold().endswith(".dds"):
                    candidates = (virtual, virtual + ".0")
                else:
                    candidates = (virtual,)
                for candidate in candidates:
                    add(candidate, "GFX resource reference")
            except (UnicodeError, ValueError):
                continue

    def parse_ui_xml(path):
        try:
            payload = resolver(path)
            if payload is None or len(payload) > _MAX_UI_XML:
                return None
            return ElementTree.fromstring(payload)
        except (OSError, ValueError, ElementTree.ParseError, TypeError):
            return None

    # UI/UIElements/<MovieName>.xml typically maps a GFx file to a UIElement.
    # Reject unrelated UIElement XML instead of attributing every image to
    # whichever movie happened to be opened.
    xml_stem = re.sub(r"[^a-z0-9]", "", base.stem.casefold())
    # Case and underscore differences are common: main_menu.gfx maps to
    # UI/UIElements/MainMenu.xml. Normalize filenames, not arbitrary paths.
    by_name = {str(p).casefold(): str(p) for p in available_paths
               if str(p).casefold().startswith("ui/") and
               str(p).casefold().endswith(".xml")}
    xml_candidates = sorted(
        p for p in by_name.values()
        if p.casefold().startswith("ui/uielements/") and
        re.sub(r"[^a-z0-9]", "", PurePosixPath(p).stem.casefold()) == xml_stem
    )
    for path in xml_candidates:
        root = parse_ui_xml(path)
        if root is None or not any(
            PurePosixPath(gfx.get("file", "")).name.casefold() == base.name.casefold()
            for gfx in root.iter("GFx")
        ):
            continue
        for element in root.iter():
            for attribute in ("file", "img", "image", "src"):
                target = element.get(attribute, "")
                if target.casefold().endswith(_IMAGE_SUFFIX):
                    try:
                        add(_virtual_path(target), "UI_Data element")
                    except ValueError:
                        pass
        break

    # These are explicit game UI data associations; they don't imply that
    # ActionScript actually displays the image in the current static frame.
    if xml_stem == "mainmenu":
        news = by_name.get("ui/mainmenunews.xml")
        if news:
            root = parse_ui_xml(news)
            if root is not None:
                for node in root.iter("News"):
                    path = node.get("img", "")
                    if path:
                        try:
                            add(_virtual_path(path), "UI_Data news")
                        except ValueError:
                            pass

    preload = by_name.get("ui/texturepreload.xml")
    if preload:
        root = parse_ui_xml(preload)
        if root is not None:
            # UI_Data's NoLevel group is the main menu's relevant preload
            # scope; other movies use common in-game preload references.
            group = root.find("NoLevel") if xml_stem == "mainmenu" else root.find("InGame")
            if group is not None:
                for node in group.iter("Preload"):
                    spec = node.get("file", "")
                    if not spec:
                        continue
                    try:
                        if "*" not in spec and "?" not in spec:
                            if spec.casefold().endswith(_IMAGE_SUFFIX):
                                add(_virtual_path(spec), "UI_Data preload")
                        elif spec.startswith("Libs/UI/"):
                            pattern = spec.casefold()
                            for key, path in entries.items():
                                if fnmatchcase(key, pattern):
                                    add(path, "UI_Data preload")
                    except ValueError:
                        pass
    return tuple(result.values())
