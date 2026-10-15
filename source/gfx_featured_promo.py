"""Guarded featured-promotion ActionScript edits for the main menu.

FFDec exports and rebuilds a private session copy. The actual game sets the
featured title at runtime; editing a static text tag alone would not suffice.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile

from gfx_edit_backend import (
    _ffdec, _replace_edit_copy, read_gfx_session, MAX_XML, MAX_SOURCE
)
from gfx_preview_backend import GfxError

MODES = frozenset(("hide", "remove", "custom"))
TARGETS = frozenset(("title", "all"))
_MARKER = "__evolveFeaturedPromoPreset"
_FUNCTION = re.compile(
    r"\bfunction\s+setFeatured\s*\([^)]*\)\s*\{|"
    r"\bsetFeatured\s*=\s*function\s*\([^)]*\)\s*\{"
)
_TARGET_NAMES = {
    "title": ("mcFeatureTitle",),
    "all": (
        "mcFeatureTitle", "mcFeatureBuy", "mcFeatureTry", "mcFeatureBg",
        "mcFeatureFocus", "mcFeatureButtonBuy", "mcFeatureButtonTry",
        "mcHighlightFeaturedMount",
    ),
}


def _skip_block(source: str, brace: int) -> int:
    """Find matching ActionScript brace without counting strings/comments."""
    depth, i, state = 0, brace, "code"
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "line":
            if ch == "\n":
                state = "code"
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 1
        elif state in ('"', "'"):
            if ch == "\\":
                i += 1
            elif ch == state:
                state = "code"
        elif ch == "/" and nxt == "/":
            state = "line"
            i += 1
        elif ch == "/" and nxt == "*":
            state = "block"
            i += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise GfxError("Cannot determine the end of setFeatured in exported ActionScript.")


def _preset_code(mode: str, target: str, custom: str) -> str:
    if mode not in MODES or target not in TARGETS:
        raise GfxError("Choose Hide, Remove, or Custom text and a supported target.")
    if mode == "custom":
        if target != "title":
            raise GfxError("Custom text applies only to the featured title.")
        if (not isinstance(custom, str) or not 1 <= len(custom) <= 72
                or custom != custom.strip() or
                any(ord(c) < 32 or ord(c) == 127 for c in custom)):
            raise GfxError("Enter 1–72 printable characters for the featured title.")
    elif custom:
        raise GfxError("Custom text is only valid for the Custom text action.")

    lines = [f"    var {_MARKER} = true;"]
    if mode == "custom":
        quoted = json.dumps(custom, ensure_ascii=True)
        lines += [
            "    if (_root.mcFeatureTitle != undefined) {",
            "        _root.mcFeatureTitle._visible = true;",
            f"        _root.mcFeatureTitle.text = {quoted};",
            "        if (_root.mcFeatureTitle.txtTitle != undefined) {",
            f"            _root.mcFeatureTitle.txtTitle.text = {quoted};",
            "        }",
            "        if (_root.mcFeatureTitle.txtText != undefined) {",
            f"            _root.mcFeatureTitle.txtText.text = {quoted};",
            "        }",
            "    }",
        ]
    else:
        for name in _TARGET_NAMES[target]:
            lines.extend((
                f"    if (_root.{name} != undefined) {{",
                f"        _root.{name}._visible = false;",
            ))
            if mode == "remove":
                lines += [
                    f"        _root.{name}.enabled = false;",
                    f"        if (_root.{name}.text != undefined) {{",
                    f"            _root.{name}.text = \"\";",
                    "        }",
                    # removeMovieClip works for dynamically attached clips;
                    # static timeline instances still use visibility fallback.
                    f"        if (_root.{name}.removeMovieClip != undefined) {{",
                    f"            _root.{name}.removeMovieClip();",
                    "        }",
                ]
            lines.append("    }")
    return "\n".join(lines) + "\n"


def patch_featured_actionscript(source: str, mode: str, target: str = "all",
                                custom: str = "") -> str:
    """Patch one identified setFeatured function without guessing UI strings."""
    if not isinstance(source, str) or len(source) > MAX_XML:
        raise GfxError("Invalid or oversized exported ActionScript.")
    if _MARKER in source:
        raise GfxError("A featured promo preset is already in this script. Undo first.")
    injection = _preset_code(mode, target, custom)
    hits = list(_FUNCTION.finditer(source))
    if len(hits) != 1:
        raise GfxError("Expected one setFeatured function in exported ActionScript.")
    begin = hits[0].end() - 1
    end = _skip_block(source, begin)
    body = source[begin + 1:end]
    if re.search(r"\breturn\b", body):
        raise GfxError(
            "setFeatured contains an early return; automatic injection "
            "could be skipped. Review this function in FFDec instead.")
    return source[:end] + "\n" + injection + source[end:]


def stage_featured_promo(workspace, session, mode: str, *,
                         target: str = "all", custom: str = "",
                         configured=None, cancelled=None):
    """Stage a guarded AS2 preset; project and installed game stay unchanged."""
    _preset_code(mode, target, custom)
    snapshot = read_gfx_session(workspace, session)
    if snapshot.relative.casefold() != "libs/ui/main_menu.gfx":
        raise GfxError("Featured promotion controls support Libs/UI/main_menu.gfx only.")
    data = snapshot.working_file.read_bytes()
    required = (b"setFeatured", b"mcFeatureTitle", b"mcFeatureBuy", b"mcFeatureTry")
    if not all(token in data for token in required):
        raise GfxError("This game build lacks the expected featured-promotion symbols.")

    with tempfile.TemporaryDirectory(prefix="gfx-featured-promo-") as temp:
        root = Path(temp)
        source_root = root / "scripts"
        source_root.mkdir()
        _ffdec(["-export", "script", str(source_root), str(snapshot.working_file)],
               configured=configured, folder=root, cancelled=cancelled)
        candidates = []
        total = 0
        for path in source_root.rglob("*"):
            if (path.is_symlink() or not path.resolve().is_relative_to(source_root)):
                raise GfxError("Unsafe FFDec ActionScript export.")
            if not path.is_file():
                continue
            if path.suffix.casefold() != ".as":
                raise GfxError("Unexpected FFDec script export extension.")
            total += path.stat().st_size
            if total > MAX_XML:
                raise GfxError("FFDec script export exceeds the bounded size.")
            raw = path.read_text(encoding="utf-8-sig")
            if _FUNCTION.search(raw):
                candidates.append((path, raw))
        if len(candidates) != 1:
            raise GfxError(
                "Could not uniquely identify the menu's setFeatured script. "
                "Use the full FFDec editor; no GFX edits have been staged.")
        file, original = candidates[0]
        updated = patch_featured_actionscript(original, mode, target, custom)
        file.write_text(updated, encoding="utf-8")
        before = root / "before.gfx"
        before.write_bytes(data)
        output = root / "rebuilt.gfx"
        _ffdec(["-importScript", str(before), str(output), str(source_root)],
               configured=configured, folder=root, cancelled=cancelled)
        if (not output.is_file() or output.is_symlink() or
                output.stat().st_size > MAX_SOURCE):
            raise GfxError("FFDec did not produce a valid rebuilt GFX movie.")
        rebuilt = output.read_bytes()
        if b"setFeatured" not in rebuilt or b"mcFeatureTitle" not in rebuilt:
            raise GfxError("FFDec rebuild lost featured symbols; no change staged.")
    return _replace_edit_copy(snapshot, rebuilt)
