"""Whole-game Asset Library import and safe mod-project handoff.

The original game is ALWAYS read-only. A broad scan is opt-in, excludes
symlinks, and imports independent signed PAKs incrementally. Mod projects
are created from verified CUSTOM-SIGNED stage copies with the existing
workspace extractor, not by modifying the reference library or forging an
RSA signature for an original-key PAK.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil

MAX_GAME_PAKS = 4096
MAX_SCAN_DEPTH = 16


@dataclass(frozen=True)
class GamePakScan:
    files: tuple[Path, ...]
    packed_bytes: int
    available_bytes: int
    already_imported: int


def _validated_game(game):
    root = Path(game).resolve(strict=True) if game else None
    if not root or not root.is_dir() or not (
        root / "bin64_SteamRetail" / "Evolve.exe").is_file():
        raise ValueError("Select the real installed EvolveGame folder first.")
    return root


def discover_game_paks(game, *, library_root=None):
    """List EVERY .pak under EvolveGame, without scanning outside the install."""
    from asset_library import imported_archives, LIBRARY_HOME
    root = _validated_game(game)
    found = []
    seen = set()
    for directory, dirs, files in os.walk(root, followlinks=False):
        here = Path(directory)
        depth = len(here.relative_to(root).parts)
        dirs[:] = sorted(
            (d for d in dirs if depth < MAX_SCAN_DEPTH
             and not (here / d).is_symlink()),
            key=str.casefold)
        for name in files:
            if Path(name).suffix.casefold() != ".pak":
                continue
            file = here / name
            if file.is_symlink() or not file.is_file():
                continue
            resolved = file.resolve()
            if not resolved.is_relative_to(root) or resolved in seen:
                continue
            seen.add(resolved)
            found.append(resolved)
            if len(found) > MAX_GAME_PAKS:
                raise ValueError(
                    "More than 4,096 PAKs found; choose a smaller EvolveGame "
                    "folder instead of extracting uncontrolled directories.")
    found.sort(key=lambda f: f.relative_to(root).as_posix().casefold())
    target = Path(library_root or LIBRARY_HOME)
    probe = target
    while not probe.exists():
        if probe.parent == probe:
            raise ValueError("No existing drive found for the Asset Library.")
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    already = {str(x["source"]) for x in imported_archives(library_root)}
    return GamePakScan(tuple(found),
                       sum(x.stat().st_size for x in found), free,
                       sum(str(x) in already for x in found))


def stage_source_for_import(import_id, stage, game=None, *, library_root=None):
    """Return (staged relative PAK path, verified candidate file).

    Only exact known signed stage-plan sources can be built and re-signed.
    The source library may have been imported under the original game key;
    the actual mod workspace must be extracted from the custom-signed stage.
    """
    from asset_library import _home, _load_manifest, _folder_for_identifier
    from universal_stage import allowed_paks
    if not re.fullmatch(r"[0-9a-f]{20}", str(import_id)):
        raise ValueError("Invalid Asset Library archive ID.")
    root = _home(library_root)
    folder = _folder_for_identifier(import_id, root)
    if folder.is_symlink() or not (folder / "asset-library.json").is_file():
        raise ValueError("The selected library archive no longer exists.")
    manifest = _load_manifest(folder)
    stage = Path(stage).resolve(strict=True)
    permitted = allowed_paks(stage)
    source = Path(manifest["source"]).resolve(strict=True)
    expected = []
    if game:
        game_root = _validated_game(game)
        if source.is_relative_to(game_root):
            expected.append(source.relative_to(game_root).as_posix().casefold())
    staged_base = (stage / "paks").resolve()
    if source.is_relative_to(staged_base):
        expected.append(source.relative_to(staged_base).as_posix().casefold())

    choices = [permitted[rel] for rel in expected if rel in permitted]
    if not choices:
        # User imported a PAK from a non-game folder. Only accept its basename
        # if the stage plan contains a SINGLE matching archive.
        basename = manifest.get("signed_name") or manifest["name"]
        matches = [rel for rel in permitted.values()
                   if Path(rel).name.casefold() == basename.casefold()]
        if len(matches) == 1:
            choices = matches
    if len(set(choices)) != 1:
        raise ValueError(
            "No unique custom-signed staged PAK matches this library archive. "
            "This is reference-only until its archive is prepared in the "
            "manager's signed staging plan.")
    relative = choices[0]
    staged_file = stage / "paks" / Path(*relative.split("/"))
    if (not staged_file.is_file() or staged_file.is_symlink()
            or not staged_file.resolve().is_relative_to(staged_base)):
        raise ValueError("Custom-signed staged copy is missing or unsafe.")
    if staged_file.name.casefold() != source.name.casefold():
        raise ValueError("Staged PAK basename differs from the imported archive.")
    key = stage / "mykeys" / "public_key.bin"
    private = stage / "mykeys" / "private_key.pem"
    if not key.is_file() or not private.is_file():
        raise FileNotFoundError("Custom signing public/private key pair is missing.")
    return relative, staged_file


def project_command(import_id, stage, workspace, game=None, *, library_root=None):
    """Return an ordinary verified workspace extraction command, no mutation."""
    import sys
    from pathlib import Path
    rel, pak = stage_source_for_import(
        import_id, stage, game, library_root=library_root)
    target = Path(workspace).resolve()
    if target.exists():
        raise FileExistsError("Editable project folder already exists.")
    if target.is_relative_to(Path(stage).resolve()):
        raise ValueError("Do not create editable projects inside signed staging.")
    script = Path(__file__).resolve().parent / "evolve_pak_workspace.py"
    args = [sys.executable, "-u", str(script), "extract",
            "--pak", str(pak), "--public-key",
            str(Path(stage) / "mykeys" / "public_key.bin"),
            "--workspace", str(target), "--skip-unsupported"]
    return rel, args
