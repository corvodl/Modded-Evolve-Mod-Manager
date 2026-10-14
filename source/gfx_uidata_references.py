"""Read-only Game/UI_Data.pak XML references for CryEngine Scaleform GFX.

UIData defines the GFx screen/movie binding and game-called functions.
TexturePreload and menu news XML contain separate texture references, but
neither establishes the bitmap/sprite coordinates inside a DDS atlas.
All names are always taken from the selected build's imported UIData PAK;
no implicit cross-build PAK mixing or arbitrary inferred bindings.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET

MAX_XML_FILES = 250
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_TOTAL_XML = 8 * 1024 * 1024
MAX_REFERENCES = 2048
_UI_PATH = re.compile(r"(?i)^ui/(?:uielements/)?[^/]+\.xml\Z")
_DDS = re.compile(r"(?i)\.(?:dds(?:\.\d+)?|tif|png)\Z")
_XML_DECL = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.I)


class UIDataError(ValueError):
    pass


def is_ui_data_archive(relative):
    """Recognize the actual Evolve archive Game/UI_Data.pak (or basename)."""
    name = PurePosixPath(str(relative).replace("\\", "/")).name
    return name.casefold() == "ui_data.pak"


def _document(relative, raw):
    if (not isinstance(relative, str) or not _UI_PATH.fullmatch(relative) or
            not isinstance(raw, bytes) or len(raw) > MAX_XML_BYTES or
            _XML_DECL.search(raw)):
        raise UIDataError("Unsafe or oversized UIData XML.")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise UIDataError("Invalid UIData XML: " + relative) from exc


def _movie_matches(requested, declared):
    """Basename-only UIData GFx refs are limited to the Libs/UI namespace."""
    movie = str(requested).replace("\\", "/").casefold().strip("/")
    name = str(declared).replace("\\", "/").casefold().strip("/")
    if not name.endswith(".gfx") or not movie.startswith("libs/ui/"):
        return False
    if "/" in name:
        return movie == name or movie == "libs/ui/" + name
    return PurePosixPath(movie).name == name


def parse_ui_data_references(movie, documents, *, archive="Game/UI_Data.pak"):
    """Parse bounded UIData XML documents, preserving evidence/provenance.

    Callbacks do not simulate ActionScript and texture preloads are explicitly
    not treated as actual sprite/bitmap links. Duplicate screen records are
    reported; the parser never silently picks a different build or movie.
    """
    if len(documents) > MAX_XML_FILES:
        raise UIDataError("Too many UIData XML documents.")
    if sum(len(data) for data in documents.values()) > MAX_TOTAL_XML:
        raise UIDataError("UIData XML collection exceeds size limit.")
    movie_name = PurePosixPath(str(movie).replace("\\", "/")).name.casefold()
    out = {
        "source_archive": archive,
        "movie": movie,
        "screens": [],
        "callbacks": [],
        "arrays": [],
        "font_sources": [],
        "texture_preloads": [],
        "named_texture_references": [],
        "menu_commands": [],
        "atlas_uv_bindings": [],
        "runtime_callbacks_executed": False,
        "status": "no-ui-data-screen-match",
        "warnings": [],
    }
    xmls = {}
    for name, raw in sorted(documents.items(), key=lambda item: item[0].casefold()):
        if not _UI_PATH.fullmatch(name):
            continue
        xmls[name.casefold()] = _document(name, raw)

    for name, root in xmls.items():
        if not name.startswith("ui/uielements/"):
            continue
        for element in root.findall(".//UIElement"):
            gfx = element.find("GFx")
            if gfx is None or not _movie_matches(movie, gfx.get("file", "")):
                continue
            screen = {
                "xml": name, "ui_element": element.get("name", ""),
                "movie": gfx.get("file"), "layer": gfx.get("layer"),
                "align": (gfx.find("Constraints/Align").attrib.copy()
                          if gfx.find("Constraints/Align") is not None else {}),
                "callbacks": [], "arrays": [],
            }
            for function in element.findall("./functions/function"):
                callback = function.get("funcname") or function.get("name")
                if not callback:
                    continue
                arguments = [p.get("name", "") for p in function.findall("./param")]
                screen["callbacks"].append({
                    "name": callback, "params": arguments})
                if len(screen["callbacks"]) > MAX_REFERENCES:
                    raise UIDataError("UIData callback count exceeds limit.")
            for record in element.findall("./arrays/array"):
                screen["arrays"].append({
                    "name": record.get("name"), "variable": record.get("varname")})
            out["screens"].append(screen)
            out["callbacks"].extend(screen["callbacks"])
            out["arrays"].extend(screen["arrays"])

    fonts = xmls.get("ui/fontmappings.xml")
    if fonts is not None:
        for language in fonts.findall(".//language"):
            filename = language.get("fontSourceFile", "")
            if filename.casefold().endswith(".gfx"):
                out["font_sources"].append({
                    "language": language.get("id"), "movie": filename,
                    "relation": "language-font-resource-not-GFX-ImportAssets"})

    preload = xmls.get("ui/texturepreload.xml")
    if preload is not None:
        for parent in preload.iter():
            for child in parent:
                if child.tag != "Preload" or "file" not in child.attrib:
                    continue
                path = child.get("file", "")
                # Keep XML scope (NoLevel, InGame, etc.) to avoid implying an
                # irrelevant in-game HUD texture is a main-menu GFX binding.
                record = {
                    "path": path, "scope": parent.tag,
                    "relation": "engine-preload-not-sprite-binding"}
                out["texture_preloads"].append(record)
                if len(out["texture_preloads"]) > MAX_REFERENCES:
                    raise UIDataError("Too many UIData texture preload entries.")

    if movie_name == "main_menu.gfx" and out["screens"]:
        # This config controls menu state; it does not reconstruct the GFX
        # artwork or know DDS atlas UVs.
        menu = xmls.get("ui/mainmenuscreen.xml")
        if menu is not None:
            for button in menu.findall(".//Button"):
                out["menu_commands"].append({
                    "command": button.get("command"),
                    "display_name": button.get("display_name"),
                    "cinematic_event": button.get("cinematic_event", ""),
                    "relation": "engine-menu-command-not-GFX-sprite-binding"})
        news = xmls.get("ui/mainmenunews.xml")
        if news is not None:
            for item in news.findall(".//News"):
                texture = item.get("img", "")
                if _DDS.search(texture):
                    out["named_texture_references"].append({
                        "path": texture, "xml": "ui/mainmenunews.xml",
                        "relation": "menu-news-image-not-direct-GFX-sprite-UV"})

    out["status"] = "ui-data-bindings-found" if out["screens"] else "no-ui-data-screen-match"
    out["warnings"].append(
        "UIData XML registers screens, callbacks and engine texture preloads; "
        "it does not expose DDS atlas regions or simulate runtime ActionScript.")
    if not out["screens"]:
        out["warnings"].append(
            "Imported UIData XML contains no explicit GFx screen binding for this movie.")
    return out
