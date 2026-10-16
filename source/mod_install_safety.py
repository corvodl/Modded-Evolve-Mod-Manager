"""Explicit mod installation preflight, read-only verification, and consented repair.

Never touches installed EvolveGame files. No automatic source edits, imports,
staging or recovery. Staging remains the ModProfiles journalled worker.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import tempfile

from universal_stage import allowed_paks, digest, validate_archive_pair


@dataclass(frozen=True)
class DiskSpacePreview:
    library_bytes: int
    stage_temp_bytes: int
    recovery_bytes: int
    volumes: tuple[tuple[str, int, int], ...]
    warnings: tuple[str, ...]
    scratch_bytes: int = 0

    @property
    def enough(self):
        return not self.warnings


def _existing(path):
    path = Path(path).resolve()
    while not path.exists():
        if path == path.parent:
            raise FileNotFoundError("No accessible storage volume for " + str(path))
        path = path.parent
    return path


def _reserve(requirements, path, count, disk_usage=shutil.disk_usage):
    ancestor = _existing(path)
    device = ancestor.stat().st_dev
    free = disk_usage(ancestor).free
    old = requirements.get(device)
    if old is None:
        requirements[device] = [str(ancestor.anchor or ancestor), count, free]
    else:
        old[1] += count


def estimate_bundle_space(bundle, stage, swap, catalog, *,
                          disk_usage=shutil.disk_usage):
    """Conservative estimate for immutable library, temp writes and backups.

    Reserve backup-before, staging temporary copy, and (for prepared swaps)
    ready-before plus a second staged temporary. Include the old currently
    applied profile's restore temporaries where present. Same-disk amounts
    are added before free-space comparison (not counted as separate disks).
    """
    from mod_profiles import ModProfiles
    from launch_integration import state_status
    stage, swap, catalog = map(lambda p: Path(p).resolve(), (stage, swap, catalog))
    if not bundle.entries:
        raise ValueError("Cannot estimate an empty mod bundle.")
    status = state_status(swap)
    if status.phase not in ("missing", "restored", "prepared"):
        raise RuntimeError("Restore or recover the active swap before installing mods.")
    imported = set()
    new_bytes = 0
    temp_bytes = 0
    backups = 0
    for entry in bundle.entries:
        ident = (entry.sha256, entry.pak_filename.casefold())
        target = stage / "paks" / Path(*entry.target.split("/"))
        if not target.is_file():
            raise FileNotFoundError("Signed staged archive is missing: " + entry.target)
        if ident not in imported:
            imported.add(ident)
            stored = catalog / "ImportedMods" / entry.sha256 / entry.pak_filename
            if not stored.is_file():
                new_bytes += entry.size
        original_size = target.stat().st_size
        prepared = status.phase == "prepared"
        # Per-PAK backup and checked temporary replacement; a prepared
        # ready-to-swap PAK doubles the number of required files.
        backups += original_size * (2 if prepared else 1)
        temp_bytes += entry.size * (2 if prepared else 1)
    store = ModProfiles(catalog)
    state = store.status()
    if state["in_progress"] or state["pending"]:
        raise RuntimeError("An earlier mod profile transaction requires recovery.")
    if state["entry_count"]:
        # Restoring old stages requires verified temp copies of backed-up PAKs.
        previous = store.applied_path
        if previous.is_file():
            import json
            meta = json.loads(previous.read_text(encoding="utf-8"))
            for item in meta.get("entries", []):
                path = Path(item["backup"]) / "before.pak"
                if not path.is_file():
                    raise RuntimeError("A prior profile backup is missing: " + str(path))
                temp_bytes += path.stat().st_size
    # ZIP review/import extraction also needs room on the OS temporary
    # filesystem, which may differ from the stage or catalog volume.
    scratch = sum(e.size for e in bundle.entries) if bundle.archive_sha else 0
    # Extra overhead covers journal metadata, filesystem growth, and partials.
    requirements = {}
    _reserve(requirements, catalog, new_bytes + max(64 * 1024**2, new_bytes // 10),
             disk_usage=disk_usage)
    _reserve(requirements, stage, temp_bytes + max(64 * 1024**2, temp_bytes // 10),
             disk_usage=disk_usage)
    _reserve(requirements, store.root / "Backups", backups + max(64 * 1024**2, backups // 10),
             disk_usage=disk_usage)
    if scratch:
        _reserve(requirements, tempfile.gettempdir(),
                 scratch + max(64 * 1024**2, scratch // 10),
                 disk_usage=disk_usage)
    rows = tuple((label, required, free) for label, required, free in requirements.values())
    warnings = tuple(f"Not enough free space on {label}: need {required // 1024**2} MiB; "
                     f"only {free // 1024**2} MiB available."
                     for label, required, free in rows if required > free)
    return DiskSpacePreview(new_bytes, temp_bytes, backups, rows, warnings, scratch)


@dataclass(frozen=True)
class ModIssue:
    mod_id: str
    target: str
    problem: str


def verify_collection(root, profile_id, stage):
    """On-demand signed source verification, collision checks and hash check."""
    from mod_profiles import ModProfiles
    root, stage = Path(root).resolve(), Path(stage).resolve()
    store = ModProfiles(root)
    profile = next((p for p in store.list_profiles() if p["id"] == profile_id), None)
    if profile is None:
        raise ValueError("Mods Collection not found.")
    stage_targets = allowed_paks(stage)
    seen = set()
    issues = []
    for mod in profile["mods"]:
        target = mod["relative"]
        if mod.get("enabled"):
            if target.casefold() in seen:
                issues.append(ModIssue(mod["id"], target, "Two enabled mods target this PAK"))
            seen.add(target.casefold())
        if target.casefold() not in stage_targets:
            issues.append(ModIssue(mod["id"], target, "Not in current signed stage"))
            continue
        source = Path(mod["source"])
        if not source.is_file():
            issues.append(ModIssue(mod["id"], target, "Missing source file"))
            continue
        if digest(source) != mod["sha256"]:
            issues.append(ModIssue(mod["id"], target, "Source SHA256 mismatch"))
            continue
        if source.name.casefold() != Path(target).name.casefold():
            issues.append(ModIssue(mod["id"], target, "Filename/target mismatch"))
            continue
        stage_plan = stage / "rekey_plan.json"
        if (mod.get("verified_stage_fingerprint") and
                stage_plan.is_file() and
                digest(stage_plan) != mod["verified_stage_fingerprint"]):
            issues.append(ModIssue(mod["id"], target, "Signed stage changed; reverify mod"))
    return tuple(issues)


def repair_mod_source(root, profile_id, mod_id, replacement, stage):
    """Relink missing/changed source only to exactly identical, signed bytes.

    Caller must obtain separate user approval before calling. Revalidates
    live profile, current signing-stage target, full expected SHA256 and RSA
    archive structure. Persists one catalog update; never stages anything.
    """
    from mod_profiles import ModProfiles, _atomic_json
    root, stage = Path(root).resolve(), Path(stage).resolve()
    replacement = Path(replacement).resolve(strict=True)
    store = ModProfiles(root)
    data = store._catalog()
    profile = store._get(data, profile_id)
    mod = store._mod(data, profile_id, mod_id)
    status = store.status()
    if status["in_progress"] or status["pending"] or status["profile_id"] == profile_id:
        raise RuntimeError("Restore or finish the current profile before repairing it.")
    if not replacement.is_file() or replacement.suffix.casefold() != ".pak":
        raise ValueError("Select a signed replacement PAK.")
    from universal_stage import select_target
    target, staged = select_target(stage, mod["relative"], replacement.name)
    if replacement == staged.resolve() or replacement.is_symlink():
        raise ValueError("Cannot use a staged or linked PAK as a replacement.")
    if digest(replacement) != mod["sha256"]:
        raise ValueError("Replacement PAK does not match the originally verified SHA256.")
    validate_archive_pair(staged, replacement, stage / "mykeys" / "public_key.bin")
    # Do not keep fragile references into downloads; verified replacement is
    # copied into the immutable shared ImportedMods library if needed.
    dest = root / "ImportedMods" / mod["sha256"] / replacement.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.is_symlink() or digest(dest) != mod["sha256"]:
            raise RuntimeError("Imported library path checksum mismatch.")
    else:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix=".repair-", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as out, replacement.open("rb") as inp:
                shutil.copyfileobj(inp, out, 4 * 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
            if digest(tmp) != mod["sha256"]:
                raise RuntimeError("Replacement changed during verified copy.")
            os.replace(tmp, dest)
        finally:
            Path(tmp).unlink(missing_ok=True)
    mod["source"] = str(dest)
    stage_plan = stage / "rekey_plan.json"
    if stage_plan.is_file():
        mod["verified_stage_fingerprint"] = digest(stage_plan)
    _atomic_json(store.catalog_path, data)
    return dest
