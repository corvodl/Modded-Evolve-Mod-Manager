"""Read-only discovery of signed map and shared PAK providers.

Missing level assets are extracted only through the verified Asset Library
importer, into a *separate* managed dependency cache. No game/archive edits.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from asset_library import _keys, _verify_pak, import_pak, imported_archives
from asset_library_bulk import discover_game_paks
from evolve_pak_workspace import normalized_name
from level_asset_mount import level_from_source

MAX_PROVIDER_ARCHIVES = 4096


def _virtual(path):
    return normalized_name(str(path).replace("\\", "/")).casefold()


def map_archive_catalog(sources):
    """Group the three common forms of map PAK by canonical map ID."""
    found = defaultdict(list)
    for source in sources:
        name = level_from_source(source)
        if name:
            found[name].append(Path(source))
    return {name: tuple(sorted(files, key=lambda f: f.as_posix().casefold()))
            for name, files in sorted(found.items())}


def needed_virtual_paths(project):
    """Find exact scene references, without basename or guessed PAK matching."""
    result = set()
    for item in project.scene:
        for path in (getattr(item, "model", ""), getattr(item, "material", "")):
            if not path:
                continue
            try:
                if path == getattr(item, "material", "") and not str(path).casefold().endswith(".mtl"):
                    path = str(path) + ".mtl"
                result.add(_virtual(path))
            except (ValueError, TypeError):
                continue
    return frozenset(result)


def providers_for_missing(sources, missing, *, stage, cancel=None, progress=None,
                          stream_roots=(), inventory_cache=None):
    """Return verified candidate PAKs for exact virtual names.

    Other maps are completely excluded (including their root-relative files).
    Ambiguous shared providers are intentionally returned rather than guessed.
    """
    keys = _keys(stage)
    indexed = defaultdict(list)
    candidates = list(sources)
    if len(candidates) > MAX_PROVIDER_ARCHIVES:
        raise ValueError("Too many archives for bounded dependency discovery.")
    target = set(missing)
    stream_roots = tuple(str(root).casefold() + '.' for root in stream_roots)
    for offset, source in enumerate(candidates):
        if cancel is not None and cancel.is_set():
            return None
        if level_from_source(source) is not None:
            continue
        if progress:
            progress("Scanning verified " + Path(source).name, offset, len(candidates))
        try:
            path = Path(source)
            # Cache only within this load, after signature verification. A
            # changed archive must be reverified before its inventory is reused.
            stamp = (str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns) if inventory_cache is not None else None
            entries = inventory_cache.get(stamp) if inventory_cache is not None else None
            if entries is None:
                info, _, _ = _verify_pak(path, keys)
                entries = tuple(info['entries'])
                if inventory_cache is not None:
                    inventory_cache[stamp] = entries
            present = set()
            for name in entries:
                try:
                    key = _virtual(name)
                except (ValueError, TypeError):
                    continue
                if key in target or any(key.startswith(root) and
                    key[len(root):].isdigit() and int(key[len(root):]) <= 9999
                    for root in stream_roots):
                    present.add(key)
            for name in sorted(present):
                indexed[name].append(Path(source))
        except (OSError, ValueError):
            continue  # Never use an archive that failed signature verification.
    return dict(indexed)



def texture_provider_candidates(reference, material_path):
    """Candidate *virtual paths* for a CryEngine material texture reference.

    Never search by basename; source-directory and cooked DDS variants are
    explicit, ordered alternatives. An unresolved alternative remains missing.
    """
    from multi_pak_assets import _candidates
    text = str(reference or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or ":" in text or ".." in text.split("/"):
        return ()
    parent = Path(material_path.replace("\\", "/")).parent.as_posix()
    prefix = []
    rooted = text.casefold().startswith((
        "characters/", "objects/", "materials/", "levels/", "libs/"))
    if rooted:
        prefix = [text]
    else:
        parts = parent.split("/") if parent != "." else []
        minimum = (3 if len(parts) >= 3 and parts[0].casefold() == "characters"
                   else 1)
        for length in range(len(parts),minimum-1,-1):
            folder = "/".join(parts[:length])
            prefix.append(folder + "/" + text)
            if "/" not in text:
                prefix.append(folder + "/textures/" + text)
        if text.casefold().startswith("textures/"):
            prefix.append(text)
    seen = set()
    result = []
    for name in prefix:
        try:
            for virtual in _candidates(name):
                key = _virtual(virtual)
                if key not in seen:
                    seen.add(key)
                    result.append(key)
        except ValueError:
            continue
    return tuple(result)


def material_dependency_candidates(project, index):
    """Inspect verified model MTL records and enumerate referenced textures.

    This reads only already-imported materials; does not decode every DDS.
    Returns ordered alternative groups, not permission to guess an asset.
    """
    from model_material_resolve import find_model_material
    from xml.etree import ElementTree as ET
    output = []
    inspected = set()
    variants_seen = set()
    for item in project.scene:
        identity = (getattr(item, "model", ""), getattr(item, "material", ""))
        if identity in variants_seen:
            continue
        variants_seen.add(identity)
        if not getattr(item, "model", ""):
            continue
        try:
            model = _virtual(item.model)
        except ValueError:
            continue
        if model not in index:
            continue
        try:
            override = getattr(item, "material", "")
            if override:
                from model_material_resolve import _material_path
                from level_asset_mount import resolve_scoped
                exact = _material_path(override, '')
                if not exact:
                    continue
                material, state = resolve_scoped(index, exact, project.workspace)
                attempted = (exact,)
            else:
                material, _, attempted = find_model_material(
                    index, model, preferred_workspace=project.workspace)
        except (OSError, ValueError):
            continue
        if material is None:
            if attempted:
                output.append(tuple(_virtual(x) for x in attempted[:24]))
            continue
        source = Path(material["file"])
        if source in inspected:
            continue
        inspected.add(source)
        try:
            if source.stat().st_size > 12*1024*1024:
                continue
            raw = source.read_bytes()
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                continue
            root = ET.fromstring(raw)
        except (OSError, ET.ParseError, ValueError):
            continue
        for texture in root.iter():
            if texture.tag.split("}")[-1].casefold() != "texture":
                continue
            reference = texture.get("File") or texture.get("file")
            if not reference:
                continue
            variants = texture_provider_candidates(
                reference, str(material.get("relative", "")))
            if variants:
                output.append(variants)
            if len(output) >= 20000:
                return tuple(output)
    return tuple(output)

def ensure_map_archives(project, game, stage, cache_root, *,
                        imported_root=None, cancel=None, progress=None):
    """Import selected-map PAKs and *required* shared providers, on a worker.

    Returns a bounded diagnostic dictionary. An unavailable game or key leaves
    already extracted assets usable; no pretend 'fully loaded' status.
    """
    result = {"imported": [], "reused": [], "missing": [], "ambiguous": [],
              "errors": [], "map_archives": [], "scanned": 0}
    if game is None or stage is None:
        result["errors"].append("Game installation or verification stage not configured")
        return result
    scan = discover_game_paks(game, library_root=imported_root)
    catalog = map_archive_catalog(scan.files)
    selection = catalog.get(str(project.level_name).casefold(), ())
    result["map_archives"] = [p.name for p in selection]
    prior = {str(p["source"]) for root in (imported_root, cache_root)
             for p in imported_archives(root)}
    def imported(source):
        if cancel is not None and cancel.is_set():
            return False
        if progress:
            progress("Importing " + source.name, 0, 1)
        try:
            if str(source) in prior:
                result["reused"].append(source.name)
            else:
                state = import_pak(source, stage=stage, root=cache_root,
                                   cancelled=cancel.is_set if cancel else None,
                                   progress=progress)
                result["imported" if state["status"] == "imported" else "reused"].append(source.name)
                prior.add(str(source))
            return True
        except (OSError, ValueError, RuntimeError) as exc:
            result["errors"].append(source.name + ": " + str(exc)[:180])
            return False
    for source in selection:
        if not imported(source) and cancel is not None and cancel.is_set():
            return result
    if cancel is not None and cancel.is_set():
        return result
    from level_asset_mount import mount_map_index
    # Each successful import can reveal another material or texture. Rebuild the
    # scoped view until the queue reaches a fixed point, with hard pass bounds.
    attempted_sources = set()
    inventory_cache = {}
    for iteration in range(32):
        if cancel is not None and cancel.is_set():
            return result
        index, _ = mount_map_index(project.workspace, project.level_name,
                                   library_root=imported_root, cache_root=cache_root)
        if hasattr(project, "layers"):
            from level_map_assembly import assemble_map_scene
            scene, diagnostics = assemble_map_scene(project, index, cancel=cancel)
            from types import SimpleNamespace
            context = SimpleNamespace(workspace=project.workspace, scene=scene)
        else:
            context = project
        groups = tuple((name,) for name in sorted(needed_virtual_paths(context)))
        groups += material_dependency_candidates(context, index)
        remaining = [p for p in scan.files if str(p) not in prior
                     and p not in attempted_sources and level_from_source(p) is None]
        needs = {x for group in groups for x in group if x not in index}
        # Include already present stream headers, even with a fallback DDS.
        roots = {x[:-2] for group in groups for x in group if x.endswith('.dds.0')}
        try:
            providers = providers_for_missing(
                remaining, needs, stage=stage, cancel=cancel, progress=progress,
                stream_roots=roots, inventory_cache=inventory_cache) if remaining and (needs or roots) else {}
        except (OSError, ValueError) as exc:
            result["errors"].append("Provider inventory: " + str(exc)[:180])
            return result
        if providers is None:
            return result
        result["scanned"] = max(result["scanned"], len(remaining))
        chosen = set()
        missing, ambiguous = [], []
        selected_streams = set()
        def select(key):
            if key in index:
                return True
            hits = providers.get(key, ())
            if len(hits) == 1:
                chosen.add(hits[0])
                return True
            if len(hits) > 1:
                ambiguous.append(key + " from " + ", ".join(str(p) for p in hits[:8]))
            return False
        for alternatives in groups:
            # A resident fallback must not prevent discovery of a stream.
            streams = [x for x in alternatives if x.endswith('.dds.0')]
            for stream in streams:
                if select(stream):
                    selected_streams.add(stream[:-2])
                    break
            if any(len(providers.get(x,())) > 1 for x in streams):
                continue
            if not any(x[:-2] in selected_streams for x in streams):
                for key in alternatives:
                    if select(key):
                        break
                    if len(providers.get(key, ())) > 1:
                        break  # Never resolve ambiguity by choosing a fallback.
                else:
                    missing.append(alternatives[0])
        for root in sorted(selected_streams):
            numbers = set()
            for key in set(index) | set(providers):
                suffix = key[len(root)+1:] if key.startswith(root + '.') else ''
                if suffix.isdigit() and int(suffix) <= 9999:
                    numbers.add(int(suffix))
                    select(key)
            for number in range(max(numbers, default=0) + 1):
                if number not in numbers:
                    missing.append('DDS fragment: ' + root + '.' + str(number))
        result["missing"] = sorted(set(missing))[:20000]
        result["ambiguous"] = sorted(set(ambiguous))[:20000]
        if not chosen:
            break
        for source in sorted(chosen, key=lambda p:p.as_posix().casefold()):
            attempted_sources.add(source)
            imported(source)
    else:
        result["errors"].append("Dependency closure reached the 32-pass limit")

    return result



def discover_map_status(game=None, *, library_roots=(), projects=()):
    """Catalog map IDs from installed PAKs, imported manifests and projects."""
    result = {}
    if game is not None:
        try:
            for name, files in map_archive_catalog(discover_game_paks(
                    game, library_root=library_roots[0] if library_roots else None
                    ).files).items():
                result.setdefault(name, {
                    "map":name,"sources":[],"imported":0,"workspace":None})[
                        "sources"].extend(str(p) for p in files)
        except (OSError, ValueError):
            pass
    for root in library_roots:
        for record in imported_archives(root):
            name = level_from_source(record["source"])
            if name:
                result.setdefault(name,{
                    "map":name,"sources":[],"imported":0,"workspace":None})[
                        "imported"] += 1
    from level_editor_core import LevelProject
    for workspace in projects:
        candidate = Path(workspace)
        if (candidate.is_dir() and not candidate.is_symlink() and
                LevelProject.contains_levels(candidate)):
            try:
                project = LevelProject(candidate)
                key = str(project.level_name).casefold()
                result.setdefault(key,{
                    "map":key,"sources":[],"imported":0,"workspace":None})[
                        "workspace"] = str(candidate.resolve())
            except (OSError, ValueError):
                continue
    for value in result.values():
        value["status"] = ("ready" if value["workspace"] else
                           "needs import" if value["imported"] else
                           "incomplete" if value["sources"] else "unavailable")
    return result


def copy_editable_map_from_library(map_name, destination, *, library_roots=(),
                                   cancel=None, progress=None):
    """Atomically copy a selected map's extracted *placement* PAK for editing.

    The copy is a read-only source-derived workspace, NOT automatically
    rebuilt/signed and NOT a replacement for the source Asset Library.
    """
    import hashlib
    import json
    import os
    import shutil
    import tempfile
    from asset_library import _home, _manifest_dirs, _load_manifest
    from evolve_pak_workspace import MANIFEST, VERSION, normalized_name
    from level_editor_core import LevelProject

    selected = str(map_name).casefold()
    dest = Path(destination).resolve()
    if dest.exists():
        if dest.is_dir() and LevelProject.contains_levels(dest):
            return dest
        raise FileExistsError("An unrelated destination already exists: " + str(dest))
    candidates = []
    for root in library_roots:
        for folder in _manifest_dirs(_home(root)):
            try:
                manifest = _load_manifest(folder)
            except (OSError,ValueError,KeyError):
                continue
            if level_from_source(manifest.get("source")) != selected:
                continue
            paths = tuple(str(e.get("path","")).casefold()
                          for e in manifest["entries"])
            source_layers = any(p.endswith(".lyr") for p in paths)
            mission = any("mission_" in p and p.endswith(".xml") for p in paths)
            if not (source_layers or mission):
                continue
            filename = Path(manifest.get("source","")).name.casefold()
            priority = (2 if source_layers else 1, filename == "level.pak")
            candidates.append((priority,folder,manifest))
    if not candidates:
        raise FileNotFoundError(
            "No imported placement/source layer archive for " + selected)
    _priority,folder,manifest = max(candidates,key=lambda x:x[0])
    if len(manifest["entries"]) > 250000:
        raise ValueError("Map copy entry limit exceeded.")
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".level-copy-",dir=dest.parent))
    copied = []
    try:
        root = (folder/"files").resolve(strict=True)
        output = temp/"files"
        total = 0
        for position, entry in enumerate(manifest["entries"]):
            if progress:
                progress("Copying " + entry["path"], position, len(manifest["entries"]))
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Map copy cancelled.")
            rel = normalized_name(entry["path"])
            source = root.joinpath(*rel.split("/"))
            if (source.is_symlink() or not source.is_file() or
                    not source.resolve().is_relative_to(root)):
                raise ValueError("Unsafe or missing imported map file: " + rel)
            size = source.stat().st_size
            total += size
            if total > 4 * 1024 ** 3:
                raise ValueError("Map project copy exceeds 4 GiB safety budget.")
            target = output.joinpath(*rel.split("/"))
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,target)
            hash_obj = hashlib.sha256()
            with target.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError('Map copy cancelled.')
                    hash_obj.update(block)
            digest=hash_obj.hexdigest()
            if size != entry["bytes"] or digest != entry["sha256"]:
                raise ValueError("Imported map file differs from verified manifest: " + rel)
            copied.append({
                "path":rel,"archive_name":rel,
                "format":"cryxml" if entry.get("cryxml") else "raw",
                "sha256":digest, "original_sha256":digest,
                "method":13})
        source_info = {
            "version":VERSION,"source_pak":manifest.get("source"),
            "source_sha256":manifest.get("source_sha256"),
            "signed_basename":manifest.get("signed_name"),
            "public_key_sha256":manifest.get("public_key_sha256"),
            "archive_entry_count":len(copied),"entries":copied,
            "reference_only":True,
        }
        (temp/MANIFEST).write_text(json.dumps(source_info,indent=2),"utf-8")
        (temp/".LEVEL-EDITOR-COPY.txt").write_text(
            "Editable copy of extracted game map data. Not a verified "
            "engine-ready or signed replacement PAK.\n",encoding="utf-8")
        if not LevelProject.contains_levels(temp):
            raise ValueError("Copied map has no compatible placements.")
        os.replace(temp,dest)
        return dest
    except BaseException:
        shutil.rmtree(temp,ignore_errors=True)
        raise
