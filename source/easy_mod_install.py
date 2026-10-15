"""Review-first import of one custom-signed Stage 2 PAK, or a ZIP containing one.

All file reads are bounded; importing never edits live game files. Applying
and undoing a profile are delegated to ModProfiles' existing journals.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import hashlib
import os
import shutil
import stat
import tempfile
import zipfile

from universal_stage import allowed_paks, validate_archive_pair

MAX_ARCHIVE_SIZE = 4 * 1024**3
MAX_ZIP_MEMBERS = 256
MAX_INFLATE = 4 * 1024**3
MAX_COMPRESSION_RATIO = 300


@dataclass(frozen=True)
class ModReview:
    source: Path
    target: str
    pak_filename: str
    size: int
    sha256: str
    changed_entries: tuple[str, ...]
    zip_member: str | None = None


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as inp:
        for data in iter(lambda: inp.read(4 * 1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def _find_mod_member(archive):
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError("ZIP is empty or contains too many entries.")
    total = 0
    pak_members = []
    for member in infos:
        path = PurePosixPath(member.filename)
        if (not member.filename or path.is_absolute() or ".." in path.parts
                or "\\" in member.filename or ":" in member.filename
                or any(part in ("", ".") for part in path.parts)):
            raise ValueError("ZIP contains an unsafe file path.")
        if member.flag_bits & 1:
            raise ValueError("Encrypted ZIP archives are not supported.")
        if stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK:
            raise ValueError("ZIP contains a symbolic link.")
        if member.is_dir():
            continue
        total += member.file_size
        if member.file_size > MAX_ARCHIVE_SIZE or total > MAX_INFLATE:
            raise ValueError("ZIP expands beyond the safe import size.")
        if member.file_size and member.file_size > max(1, member.compress_size) * MAX_COMPRESSION_RATIO:
            raise ValueError("ZIP expansion ratio exceeds the safe limit.")
        if path.suffix.casefold() == ".pak":
            pak_members.append(member)
    if len(pak_members) != 1:
        raise ValueError("Mod ZIP must contain exactly one signed PAK; use individual packages for multi-PAK mods.")
    return pak_members[0]


def _extract_single_pak(archive, member, destination):
    with archive.open(member) as src, Path(destination).open("xb") as out:
        copied = 0
        while True:
            block = src.read(4 * 1024 * 1024)
            if not block:
                break
            copied += len(block)
            if copied > member.file_size or copied > MAX_ARCHIVE_SIZE:
                raise ValueError("ZIP expansion changed during import.")
            out.write(block)
        if copied != member.file_size:
            raise ValueError("ZIP entry size changed during import.")


def _locate_target(stage, name):
    candidates = sorted(
        rel for rel in allowed_paks(Path(stage)).values()
        if Path(rel).name.casefold() == name.casefold())
    if len(candidates) == 0:
        raise ValueError("PAK basename is not a signed target in the selected game stage.")
    if len(candidates) > 1:
        raise ValueError("Multiple targets share the PAK basename; choose an unambiguous PAK before importing.")
    return candidates[0]


def review_package(source, stage):
    """Verify filename, exact RSA-signed CDR layout, and modified asset names."""
    source = Path(source).resolve(strict=True)
    stage = Path(stage).resolve(strict=True)
    if source.suffix.casefold() not in (".pak", ".zip") or not source.is_file():
        raise ValueError("Choose a PAK or ZIP mod package.")
    if source.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValueError("Mod package is too large for the safe import limit.")
    key = stage / "mykeys" / "public_key.bin"
    if not key.is_file():
        raise FileNotFoundError("Signing public key is not configured. Run Manager Setup.")
    with tempfile.TemporaryDirectory(prefix="evolve-mod-review-") as temp:
        pak = source
        member_name = None
        if source.suffix.casefold() == ".zip":
            with zipfile.ZipFile(source) as archive:
                member = _find_mod_member(archive)
                member_name = member.filename
                pak = Path(temp) / Path(member_name).name
                _extract_single_pak(archive, member, pak)
        target = _locate_target(stage, pak.name)
        baseline = stage / "paks" / Path(*target.split("/"))
        if not baseline.is_file():
            raise FileNotFoundError("Selected stage is missing the target original PAK.")
        changed = validate_archive_pair(baseline, pak, key)
        return ModReview(source, target, pak.name, pak.stat().st_size,
                         _hash_file(pak), tuple(changed), member_name)


def import_reviewed_mod(review, stage, catalog_root, profile_id):
    """Recheck changed input and add immutable library copy to existing profile.

    Does not call apply(); actual stage writes require a separate user
    confirmation through the existing ModProfiles worker.
    """
    from mod_profiles import ModProfiles
    from launch_integration import state_status
    if not isinstance(review, ModReview):
        raise TypeError("Expected a verified ModReview.")
    stage = Path(stage).resolve(strict=True)
    store = ModProfiles(catalog_root)
    if state_status(stage.parent / "swap").phase in ("swapped", "swapping"):
        # The caller still validates its actual configured swap directory
        # via ModProfiles.apply; this check is advisory only.
        pass
    with tempfile.TemporaryDirectory(prefix="evolve-mod-import-") as temp:
        source = review.source.resolve(strict=True)
        if source.suffix.casefold() == ".zip":
            with zipfile.ZipFile(source) as archive:
                member = _find_mod_member(archive)
                if member.filename != review.zip_member:
                    raise RuntimeError("ZIP contents changed since review. Review again.")
                candidate = Path(temp) / review.pak_filename
                _extract_single_pak(archive, member, candidate)
        else:
            candidate = source
        if _hash_file(candidate) != review.sha256:
            raise RuntimeError("Mod file changed since review; no mod was imported.")
        # Validate against current signed stage again, including where another
        # mod might have changed its archive while confirmation was open.
        if _locate_target(stage, candidate.name) != review.target:
            raise RuntimeError("Signing stage target changed; review package again.")
        validate_archive_pair(stage / "paks" / Path(*review.target.split("/")),
                              candidate, stage / "mykeys" / "public_key.bin")
        destination = Path(catalog_root).resolve() / "ImportedMods" / review.sha256[:20] / review.pak_filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_symlink() or _hash_file(destination) != review.sha256:
                raise RuntimeError("Imported library path already contains different data.")
        else:
            staging = destination.with_name(destination.name + ".new")
            try:
                with candidate.open("rb") as inp, staging.open("xb") as out:
                    shutil.copyfileobj(inp, out, 4 * 1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                if _hash_file(staging) != review.sha256:
                    raise RuntimeError("Imported mod checksum mismatch.")
                os.replace(staging, destination)
            finally:
                staging.unlink(missing_ok=True)
        mod_id = store.add_mod(profile_id, destination, review.target, stage)
        return mod_id, destination
