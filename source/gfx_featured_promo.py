"""Guarded featured-promotion ActionScript edits for the main menu.

FFDec exports and rebuilds a private session copy. The actual game sets the
featured title at runtime; editing a static text tag alone would not suffice.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import tempfile

from gfx_edit_backend import (
    _ffdec, _replace_edit_copy, read_gfx_session, MAX_XML, MAX_SOURCE
)
from gfx_preview_backend import GfxError

MODES = frozenset(("hide", "remove", "custom", "layout"))
TARGETS = frozenset(("title", "try", "buy", "all"))
_MARKER = "__evolveFeaturedPromoPreset"
_FUNCTION = re.compile(
    r"\bfunction\s+setFeatured\s*\([^)]*\)\s*\{|"
    r"\bsetFeatured\s*=\s*function\s*\([^)]*\)\s*\{"
)
_TARGET_NAMES = {
    "title": ("mcFeatureTitle",),
    "try": ("mcFeatureTry", "mcFeatureButtonTry"),
    "buy": ("mcFeatureBuy", "mcFeatureButtonBuy"),
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


def _preset_code(mode: str, target: str, custom: str, layout=None) -> str:
    if mode == "layout":
        if target not in ("title", "try", "buy") or custom:
            raise GfxError("Layout overrides support one featured title, TRY or BUY control.")
        if not isinstance(layout, (tuple, list)) or len(layout) != 6:
            raise GfxError("Layout properties must contain X/Y, scale, rotation and visibility.")
        x, y, sx, sy, angle, visible = layout
        if (not isinstance(visible, bool) or
                not all(isinstance(n, (float, int)) and math.isfinite(n)
                        for n in (x, y, sx, sy, angle)) or
                not (-100000 <= x <= 100000 and -100000 <= y <= 100000 and
                     .01 <= sx <= 20 and .01 <= sy <= 20 and
                     -360 <= angle <= 360)):
            raise GfxError("Featured layout values exceed the supported bounds.")
        # These values are absolute overrides. Game callbacks may still
        # replace them later; we do not pretend to know their runtime state.
        names = {"title": ("mcFeatureTitle",),
                 "try": ("mcFeatureTry", "mcFeatureButtonTry"),
                 "buy": ("mcFeatureBuy", "mcFeatureButtonBuy")}[target]
        lines = [f"    var {_MARKER}_{target} = true;"]
        for name in names:
            lines += [
                f"    if (_root.{name} != undefined) {{",
                f"        _root.{name}._x = {x:.4f};",
                f"        _root.{name}._y = {y:.4f};",
                f"        _root.{name}._xscale = {sx * 100:.4f};",
                f"        _root.{name}._yscale = {sy * 100:.4f};",
                f"        _root.{name}._rotation = {angle:.4f};",
                f"        _root.{name}._visible = {'true' if visible else 'false'};",
                "    }",
            ]
        return "\n".join(lines) + "\n"
    if layout is not None:
        raise GfxError("Layout properties require the Layout override mode.")
    if mode not in MODES or target not in TARGETS:
        raise GfxError("Choose Hide, Remove, Custom text, or Layout override.")
    if mode == "custom":
        if target not in ("title", "try", "buy"):
            raise GfxError("Custom text requires the featured title, TRY or BUY control.")
        if (not isinstance(custom, str) or not 1 <= len(custom) <= 160
                or custom != custom.strip() or
                any(not c.isprintable() for c in custom)):
            raise GfxError("Enter 1–160 printable characters for the selected text.")
    elif custom:
        raise GfxError("Custom text is only valid for the Custom text action.")

    lines = [f"    var {_MARKER}_{target} = true;"]
    if mode == "custom":
        # These known featured movie instances are real runtime targets.
        # Their internal TextField names depend on the game build; never
        # create a synthetic text field or replace unrelated button labels.
        quoted = json.dumps(custom, ensure_ascii=True)
        child_fields = ("txtTitle", "txtText", "txtLabel", "txtTry",
                        "txtBuy", "label", "textField")
        for name in _TARGET_NAMES[target]:
            lines.extend((
                f"    if (_root.{name} != undefined) {{",
                f"        _root.{name}._visible = true;",
            ))
            # Retain compatibility with existing direct title text targets.
            if target == "title":
                lines.append(f"        _root.{name}.text = {quoted};")
            else:
                lines.extend((
                    f"        if (_root.{name}.text != undefined) {{",
                    f"            _root.{name}.text = {quoted};",
                    "        }",
                ))
            for field in child_fields:
                lines.extend((
                    f"        if (_root.{name}.{field} != undefined && "
                    f"_root.{name}.{field}.text != undefined) {{",
                    f"            _root.{name}.{field}.text = {quoted};",
                    "        }",
                ))
            lines.append("    }")
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
                                custom: str = "", layout=None) -> str:
    """Patch one identified setFeatured function without guessing UI strings."""
    if not isinstance(source, str) or len(source) > MAX_XML:
        raise GfxError("Invalid or oversized exported ActionScript.")
    injection = _preset_code(mode, target, custom, layout)
    # A different control can be edited in the same session. Each preset is
    # explicitly scoped so hiding TRY/BUY never forces a reset of the title.
    # The older single-marker preset has unknown targets: fail closed.
    if re.search(r"\bvar\s+" + _MARKER + r"\s*=", source):
        raise GfxError(
            "An older, unscoped promo preset is present. Undo it before "
            "using independent title, TRY and BUY edits.")
    if re.search(r"\bvar\s+" + _MARKER + "_" + target + r"\s*=", source):
        raise GfxError(
            "A featured edit for this control is already staged. "
            "Undo that edit before changing the same control again.")
    if (target == "all" and
            any(re.search(r"\bvar\s+" + _MARKER + "_" + item + r"\s*=", source)
                for item in ("title", "try", "buy"))):
        raise GfxError("Individual title/TRY/BUY edits are staged. Undo before editing the entire promotion.")
    if target != "all" and re.search(
            r"\bvar\s+" + _MARKER + r"_all\s*=", source):
        raise GfxError("An entire-promotion edit is staged. Undo before editing individual controls.")
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
                         target: str = "all", custom: str = "", layout=None,
                         configured=None, cancelled=None):
    """Stage a guarded AS2 preset; project and installed game stay unchanged."""
    _preset_code(mode, target, custom, layout)
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
        total, file_count = 0, 0
        safe_root = source_root.resolve()
        for path in source_root.rglob("*"):
            if (path.is_symlink() or not path.resolve().is_relative_to(safe_root)):
                raise GfxError("Unsafe FFDec ActionScript export.")
            if not path.is_file():
                continue
            if path.suffix.casefold() != ".as":
                raise GfxError("Unexpected FFDec script export extension.")
            file_count += 1
            total += path.stat().st_size
            if total > MAX_XML or file_count > 2000:
                raise GfxError("FFDec script export exceeds the bounded size.")
            raw = path.read_text(encoding="utf-8-sig")
            if _FUNCTION.search(raw):
                candidates.append((path, raw))
        if len(candidates) != 1:
            raise GfxError(
                "Could not uniquely identify the menu's setFeatured script. "
                "Use the full FFDec editor; no GFX edits have been staged.")
        file, original = candidates[0]
        updated = patch_featured_actionscript(original, mode, target, custom, layout)
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
