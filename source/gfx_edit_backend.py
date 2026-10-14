"""Validated editable Scaleform GFX workspace sessions (FFDec 26.3.0).

An editor session is an expendable *copy* of a manifest-listed GFX file.
Resources are replaced using documented FFDec CLI commands; its external GUI
can author advanced shapes, timeline changes and ActionScript. The manager
only commits a rebuilt GFX after explicit user approval, strict validation,
a hash-checked baseline and the existing EditorBackups atomic replacement.

No changes to Asset Library imports or installed game PAKs are permitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from gfx_preview_backend import (
    GfxError, MAX_SOURCE, inspect_gfx, ffdec_command, safe_import_path, _execute
)
from workspace_file_actions import checked_path, replace_raw_file, sha_file
from evolve_pak_workspace import normalized_name

EDIT_ROOT = "GfxEditSessions"
EDIT_JSON = "edit-session.json"
MAX_XML = 64 * 1024 * 1024
MAX_RESOURCE = 32 * 1024 * 1024
REPLACE_TIMEOUT = 75
RESOURCE_EXT = {
    "image": frozenset((".png", ".jpg", ".jpeg")),
    "shape": frozenset((".svg",)),
    "text": frozenset((".txt",)),
    "sound": frozenset((".mp3", ".wav")),
    "binary": frozenset((".bin",)),
}
SHA = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True)
class EditSnapshot:
    session: Path
    relative: str
    original_sha256: str
    working_file: Path
    edited_sha256: str
    edits: int
    changed: bool


def _source_path(workspace, relative):
    virtual = normalized_name(relative)
    if not virtual.casefold().endswith(".gfx"):
        raise GfxError("Only .gfx movies can be edited by the Scaleform workflow.")
    ws = Path(workspace).resolve(strict=True)
    manifest = ws / ".evolve-pak-workspace.json"
    info = json.loads(manifest.read_text(encoding="utf-8"))
    if sum(normalized_name(x["path"]).casefold() == virtual.casefold()
           for x in info.get("entries", ())) != 1:
        raise GfxError("Selected GFX is not a unique file in this editable PAK project.")
    root = ws / "files"
    if root.is_symlink():
        raise GfxError("Editing a symlinked project file directory is forbidden.")
    source = checked_path(ws, virtual)
    if source.is_symlink() or source.stat().st_size > MAX_SOURCE:
        raise GfxError("Missing, linked or oversized GFX movie.")
    return ws, virtual, source


def _validate(data, *, relative):
    if not isinstance(data, bytes) or len(data) > MAX_SOURCE:
        raise GfxError("Edited GFX exceeds the 12 MiB safety limit.")
    info = inspect_gfx(data)
    for url in info.imports:
        safe_import_path(url, relative)
    return info


def _dir(workspace):
    ws = Path(workspace).resolve(strict=True)
    base = ws / EDIT_ROOT
    if base.is_symlink() or (base.exists() and not base.is_dir()):
        raise GfxError("Unsafe GFX edit session directory.")
    base.mkdir(mode=0o700, exist_ok=True)
    if base.is_symlink():
        raise GfxError("GFX edit session root is a symbolic link.")
    return base


def create_gfx_session(workspace, relative, *, dependencies=None):
    """Create a private project-local copy. Never opens or edits live game data."""
    ws, virtual, src = _source_path(workspace, relative)
    original = src.read_bytes()
    _validate(original, relative=virtual)
    base = _dir(ws)
    folder = base / uuid4().hex
    folder.mkdir(mode=0o700)
    editable = folder / "movie.gfx"
    editable.write_bytes(original)
    # Only imports explicitly referenced by this movie may accompany the
    # editable copy. Keep relative names inside the external editor sandbox.
    from gfx_preview_backend import _validate_imported_movie
    info = inspect_gfx(original)
    accepted = {
        safe_import_path(url, virtual): url
        for url in info.imports
    }
    dependencies = dependencies or {}
    if len(dependencies) > len(accepted):
        raise GfxError("Unexpected external GFX editor dependencies.")
    for full_name, payload in dependencies.items():
        if full_name not in accepted:
            raise GfxError("Unreferenced movie dependency is not part of this editing session.")
        _validate_imported_movie(payload, full_name)
        dest = (folder / accepted[full_name]).resolve()
        if dest == editable or not dest.is_relative_to(folder.resolve()):
            raise GfxError("Unsafe imported movie filename.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
    digest = hashlib.sha256(original).hexdigest()
    record = {
        "version": 1, "relative": virtual, "original_sha256": digest,
        "working_file": "movie.gfx", "edits": 0
    }
    (folder / EDIT_JSON).write_text(
        json.dumps(record, indent=2), encoding="utf-8")
    return EditSnapshot(folder, virtual, digest, editable, digest, 0, False)


def read_gfx_session(workspace, session):
    ws = Path(workspace).resolve(strict=True)
    base = ws / EDIT_ROOT
    path = Path(session).resolve(strict=True)
    if (not base.is_dir() or base.is_symlink() or
            path.parent != base.resolve(strict=True) or
            not path.is_dir() or path.is_symlink()):
        raise GfxError("GFX session does not belong to this editable workspace.")
    record_path = path / EDIT_JSON
    if record_path.is_symlink():
        raise GfxError("GFX edit session metadata is not a regular file.")
    info = json.loads(record_path.read_text(encoding="utf-8"))
    if (info.get("version") != 1 or info.get("working_file") != "movie.gfx" or
            not isinstance(info.get("edits"), int) or
            not 0 <= info["edits"] <= 500 or
            not SHA.fullmatch(str(info.get("original_sha256")))):
        raise GfxError("Invalid GFX edit session metadata.")
    ws, virtual, original = _source_path(ws, info["relative"])
    path_to_edit = path / "movie.gfx"
    if (path_to_edit.is_symlink() or not path_to_edit.is_file() or
            path_to_edit.stat().st_size > MAX_SOURCE):
        raise GfxError("GFX edit copy is missing, linked or oversized.")
    data = path_to_edit.read_bytes()
    _validate(data, relative=virtual)
    digest = hashlib.sha256(data).hexdigest()
    return EditSnapshot(path, virtual, info["original_sha256"], path_to_edit,
                        digest, info["edits"], digest != info["original_sha256"])


def _replace_edit_copy(snapshot, payload):
    """Commit a validated edited copy to the isolated session, not the PAK."""
    _validate(payload, relative=snapshot.relative)
    folder = snapshot.session
    if sha_file(snapshot.working_file) != snapshot.edited_sha256:
        raise GfxError("GFX edit copy changed while the operation was running.")
    if hashlib.sha256(payload).hexdigest() == snapshot.edited_sha256:
        raise GfxError("FFDec produced an unchanged GFX; no replacement was committed.")
    history = folder / "undo"
    if history.is_symlink():
        raise GfxError("Unsafe GFX session undo directory.")
    history.mkdir(exist_ok=True)
    undo = history / ("%04d.gfx" % snapshot.edits)
    with undo.open("xb") as out:
        out.write(snapshot.working_file.read_bytes())
    with tempfile.NamedTemporaryFile(
            mode="wb", prefix="rebuild-", suffix=".gfx",
            dir=folder, delete=False) as out:
        temp_path = Path(out.name)
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())
    try:
        os.replace(temp_path, snapshot.working_file)
        meta = folder / EDIT_JSON
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["edits"] += 1
        meta.write_text(json.dumps(data, indent=2), encoding="utf-8")
    finally:
        temp_path.unlink(missing_ok=True)
    return read_gfx_session(folder.parent.parent, folder)


def _ffdec(args, *, configured, folder, cancelled=None):
    binary = ffdec_command(configured)
    # Every input/output is a private session copy, never installed game data.
    _execute([*binary, "-cli", *args], cwd=folder,
             cancelled=cancelled, timeout=REPLACE_TIMEOUT)


def replace_gfx_resource(workspace, session, kind, character_id, imported_file,
                         *, configured=None, cancelled=None):
    """Replace a native tag using FFDec, rebuild and validate before staging.

    'image', 'shape', 'text', 'sound', 'binary' are supported only when the
    character ID belongs to the corresponding original tag. FFDec's advanced
    GUI handles visual script, timeline, button and tag editing.
    """
    snap = read_gfx_session(workspace, session)
    movie = snap.working_file.read_bytes()
    info = _validate(movie, relative=snap.relative)
    if kind not in RESOURCE_EXT or not isinstance(character_id, int) or character_id <= 0:
        raise GfxError("Select a supported existing GFX resource.")
    if (kind, character_id) not in info.resources:
        raise GfxError("Selected character ID does not match an existing " + kind + " resource.")
    selected = Path(imported_file).expanduser().resolve(strict=True)
    if (selected.is_symlink() or not selected.is_file() or
            selected.suffix.casefold() not in RESOURCE_EXT[kind] or
            selected.stat().st_size > MAX_RESOURCE or selected == snap.working_file):
        raise GfxError("Resource must be a separate, supported image/text/shape/audio file.")
    if kind == "image":
        from io import BytesIO
        from PIL import Image
        with Image.open(selected) as picture:
            if picture.width * picture.height > 36_000_000:
                raise GfxError("Replacement image exceeds safe dimensions.")
            picture.verify()
    with tempfile.TemporaryDirectory(prefix="gfx-resource-") as tmp:
        isolated = Path(tmp)
        input_data = isolated / ("input" + selected.suffix.casefold())
        shutil.copyfile(selected, input_data)
        existing = isolated / "before.gfx"
        existing.write_bytes(movie)
        output = isolated / "rebuilt.gfx"
        _ffdec(["-replace", str(existing), str(output),
                str(character_id), str(input_data)],
               configured=configured, folder=isolated, cancelled=cancelled)
        if not output.is_file() or output.stat().st_size > MAX_SOURCE:
            raise GfxError("FFDec did not generate a bounded rebuilt GFX movie.")
        result = output.read_bytes()
    return _replace_edit_copy(snap, result)


def export_gfx_xml(workspace, session, destination, *, configured=None,
                   cancelled=None):
    """Export FFDec tag-structure XML to a NEW file for advanced authoring."""
    snap = read_gfx_session(workspace, session)
    dest = Path(destination).expanduser().resolve()
    if dest.suffix.casefold() != ".xml" or not dest.parent.is_dir() or dest.exists():
        raise GfxError("Choose a new XML filename in an existing directory.")
    with tempfile.TemporaryDirectory(prefix="gfx-tags-") as tmp:
        out = Path(tmp) / "movie.xml"
        _ffdec(["-swf2xml", str(snap.working_file), str(out)],
               configured=configured, folder=Path(tmp), cancelled=cancelled)
        if not out.is_file() or out.stat().st_size > MAX_XML:
            raise GfxError("FFDec did not produce a bounded editable XML document.")
        with dest.open("xb") as writer, out.open("rb") as source:
            shutil.copyfileobj(source, writer)
    return dest


def rebuild_gfx_from_xml(workspace, session, imported_xml, *,
                         configured=None, cancelled=None):
    """Rebuild XML-tag edits in FFDec. No project write until approved."""
    snap = read_gfx_session(workspace, session)
    xml = Path(imported_xml).expanduser().resolve(strict=True)
    if xml.suffix.casefold() != ".xml" or not xml.is_file() or xml.stat().st_size > MAX_XML:
        raise GfxError("Select a bounded FFDec XML tag document.")
    with tempfile.TemporaryDirectory(prefix="gfx-xml-roundtrip-") as tmp:
        folder = Path(tmp)
        infile = folder / "import.xml"
        shutil.copyfile(xml, infile)
        output = folder / "rebuilt.gfx"
        _ffdec(["-xml2swf", str(infile), str(output)],
               configured=configured, folder=folder, cancelled=cancelled)
        if not output.is_file() or output.stat().st_size > MAX_SOURCE:
            raise GfxError("FFDec did not create a safe rebuilt GFX movie.")
        content = output.read_bytes()
    return _replace_edit_copy(snap, content)


def use_edited_gfx_file(workspace, session, candidate):
    """Import a GFX authored in FFDec GUI or another external editor."""
    snap = read_gfx_session(workspace, session)
    path = Path(candidate).expanduser().resolve(strict=True)
    if path.suffix.casefold() != ".gfx" or path.is_symlink() or not path.is_file():
        raise GfxError("Choose a separately edited .gfx file.")
    if path.stat().st_size > MAX_SOURCE:
        raise GfxError("Edited GFX exceeds safety limit.")
    return _replace_edit_copy(snap, path.read_bytes())


def undo_gfx_session(workspace, session):
    snap = read_gfx_session(workspace, session)
    if snap.edits < 1:
        raise GfxError("There are no staged GFX edits to undo.")
    history = snap.session / "undo" / ("%04d.gfx" % (snap.edits - 1))
    if history.is_symlink() or not history.is_file():
        raise GfxError("Previous GFX edit snapshot is not available.")
    prior = history.read_bytes()
    _validate(prior, relative=snap.relative)
    if sha_file(snap.working_file) != snap.edited_sha256:
        raise GfxError("GFX edit copy changed while undoing.")
    with tempfile.NamedTemporaryFile(mode="wb", dir=snap.session,
                                      prefix="undo-rebuild-", delete=False) as stream:
        pending = Path(stream.name)
        stream.write(prior)
    try:
        os.replace(pending, snap.working_file)
        meta = snap.session / EDIT_JSON
        state = json.loads(meta.read_text(encoding="utf-8"))
        state["edits"] -= 1
        meta.write_text(json.dumps(state, indent=2), encoding="utf-8")
        history.unlink()
    finally:
        pending.unlink(missing_ok=True)
    return read_gfx_session(workspace, session)


def apply_gfx_session(workspace, session, *, expected_sha256=None):
    """Validated project-only commit with automatic original EditorBackups.

    A draft does not modify the PAK. Subsequent build/sign/install uses the
    existing reviewed mod workflow with its own offline-only safeguards.
    """
    snap = read_gfx_session(workspace, session)
    ws, virtual, source = _source_path(workspace, snap.relative)
    if expected_sha256 and expected_sha256 != snap.original_sha256:
        raise GfxError("Current edit session does not match the selected original.")
    if sha_file(source) != snap.original_sha256:
        raise GfxError("Workspace GFX changed since this edit session began. Review again.")
    if not snap.changed:
        raise GfxError("GFX edit session contains no changes to apply.")
    _validate(snap.working_file.read_bytes(), relative=virtual)
    changed = replace_raw_file(ws, virtual, snap.working_file,
                               snap.original_sha256)
    return {"changed": changed, "relative": virtual,
            "sha256": sha_file(source), "backup_root": str(ws / "EditorBackups"),
            "game_modified": False, "pak_modified": False}


def ffdec_gui_command(configured):
    """Open the full authoring UI from a trusted external FFDec installation."""
    command = ffdec_command(configured)
    binary = Path(command[-1] if command[-1].endswith(".jar") else command[0])
    if binary.suffix.casefold() == ".jar":
        return command
    # Windows bundled launcher is for the GUI. CLI entrypoint is not.
    gui = binary.with_name("ffdec.exe")
    if gui.is_file() and not gui.is_symlink():
        return (str(gui),)
    jar = binary.with_name("ffdec.jar")
    if jar.is_file() and not jar.is_symlink():
        return ffdec_command(str(jar))
    raise GfxError("FFDec GUI is unavailable. Install FFDec with ffdec.exe or its JAR and Java.")


def launch_gfx_gui(workspace, session, *, configured=None):
    snap = read_gfx_session(workspace, session)
    binary = ffdec_gui_command(configured)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    child = subprocess.Popen(
        [*binary, str(snap.working_file)], cwd=str(snap.session),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    return child.pid
