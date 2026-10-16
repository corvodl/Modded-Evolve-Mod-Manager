"""Review entire signed multi-PAK ZIPs and existing mod folders before importing.

A bundle is validated completely before one atomic ModProfiles catalog update.
Actual staging remains a separate journalled, recoverable ModProfiles worker.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from contextlib import ExitStack
import hashlib
import os
import shutil
import stat
import tempfile
import zipfile

from easy_mod_install import (
    ModReview, review_package, _hash_file, _extract_single_pak, _locate_target,
    MAX_ARCHIVE_SIZE, MAX_ZIP_MEMBERS, MAX_INFLATE, MAX_COMPRESSION_RATIO
)
from universal_stage import allowed_paks, validate_archive_pair


@dataclass(frozen=True)
class BundleReview:
    source: Path
    entries: tuple[ModReview, ...]
    archive_sha: str | None = None
    legacy: bool = False


def _reject_raw_zip_names(archive):
    """Inspect central AND local names before ZipInfo normalizes separators.

    ZipInfo replaces the host OS separator with "/" on Windows, so testing
    only ZipInfo.filename can mistakenly accept an original "Game\libs.pak".
    Read the original byte names directly from the existing ZipFile handle;
    preserve its position for subsequent normal zipfile extraction.
    """
    stream = archive.fp
    if stream is None:
        raise ValueError("ZIP archive is closed.")
    position = stream.tell()
    try:
        stream.seek(archive.start_dir)
        entries = archive.infolist()
        for entry in entries:
            header = stream.read(zipfile.sizeCentralDir)
            if len(header) != zipfile.sizeCentralDir:
                raise ValueError("Truncated ZIP central directory.")
            fields = zipfile.struct.unpack(zipfile.structCentralDir, header)
            if fields[zipfile._CD_SIGNATURE] != zipfile.stringCentralDir:
                raise ValueError("Malformed ZIP central directory.")
            filename_size = fields[zipfile._CD_FILENAME_LENGTH]
            extra_size = fields[zipfile._CD_EXTRA_FIELD_LENGTH]
            comment_size = fields[zipfile._CD_COMMENT_LENGTH]
            raw = stream.read(filename_size)
            if len(raw) != filename_size:
                raise ValueError("Truncated ZIP central filename.")
            if b"\\" in raw or any(ch < 32 for ch in raw):
                raise ValueError("ZIP contains an unsafe, unnormalized filename.")
            stream.seek(extra_size + comment_size, 1)
        # A malicious archive can provide a safe central directory filename
        # but an unsafe name in its local header, so check both representations.
        for entry in entries:
            stream.seek(entry.header_offset)
            local = stream.read(zipfile.sizeFileHeader)
            if len(local) != zipfile.sizeFileHeader:
                raise ValueError("Truncated ZIP local header.")
            fields = zipfile.struct.unpack(zipfile.structFileHeader, local)
            if fields[zipfile._FH_SIGNATURE] != zipfile.stringFileHeader:
                raise ValueError("Malformed ZIP local header.")
            filename_size = fields[zipfile._FH_FILENAME_LENGTH]
            raw = stream.read(filename_size)
            if len(raw) != filename_size:
                raise ValueError("Truncated ZIP local filename.")
            if b"\\" in raw or any(ch < 32 for ch in raw):
                raise ValueError("ZIP contains an unsafe, unnormalized filename.")
    finally:
        stream.seek(position)


def _members(archive):
    infos = archive.infolist()
    if not infos or len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError("ZIP is empty or contains too many entries.")
    _reject_raw_zip_names(archive)
    total = 0
    paks = []
    names = set()
    for entry in infos:
        name = entry.filename
        normalized = PurePosixPath(name)
        if (not name or name.startswith(("/", "\\"))
                or ":" in name or "\\" in name or ".." in normalized.parts
                or any(part in (".", "") for part in normalized.parts)
                or entry.flag_bits & 1
                or stat.S_IFMT(entry.external_attr >> 16) == stat.S_IFLNK):
            raise ValueError("ZIP contains an unsafe, encrypted or linked path.")
        if entry.is_dir():
            continue
        if name.casefold() in names:
            raise ValueError("ZIP contains duplicate entry names.")
        names.add(name.casefold())
        total += entry.file_size
        if (entry.file_size > MAX_ARCHIVE_SIZE or total > MAX_INFLATE
                or (entry.file_size and
                    entry.file_size > max(1, entry.compress_size) * MAX_COMPRESSION_RATIO)):
            raise ValueError("ZIP expansion exceeds the safe size/ratio limit.")
        if normalized.suffix.casefold() == ".pak":
            paks.append(entry)
    if not paks or len(paks) > 24:
        raise ValueError("A bundle must contain 1–24 signed PAK files.")
    return tuple(paks)


def _target(stage, name):
    # Exact stage-relative archive path is accepted for basename collisions
    # only when explicitly supplied in the ZIP. Never guess based on order.
    plan = tuple(allowed_paks(stage).values())
    suffix = PurePosixPath(name).parts
    exact = [p for p in plan if tuple(x.casefold() for x in p.split("/"))
             == tuple(x.casefold() for x in suffix)]
    if len(exact) == 1:
        return exact[0]
    return _locate_target(stage, Path(name).name)


def _verify(path, target, stage):
    baseline = stage / "paks" / Path(*target.split("/"))
    key = stage / "mykeys" / "public_key.bin"
    if not baseline.is_file() or not key.is_file():
        raise FileNotFoundError("Signed target or public key is missing for " + target)
    changed = validate_archive_pair(baseline, path, key)
    return tuple(changed)


def review_mod_bundle(source, stage):
    source = Path(source).resolve(strict=True)
    stage = Path(stage).resolve(strict=True)
    if source.suffix.casefold() == ".pak":
        return BundleReview(source, (review_package(source, stage),))
    if source.suffix.casefold() != ".zip" or not source.is_file():
        raise ValueError("Choose a signed PAK or ZIP mod bundle.")
    if source.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValueError("The ZIP is larger than the safe import limit.")
    archive_sha = _hash_file(source)
    entries, seen_targets = [], set()
    with tempfile.TemporaryDirectory(prefix="evolve-bundle-verify-") as temp:
        with zipfile.ZipFile(source) as archive:
            for index, member in enumerate(_members(archive)):
                candidate = Path(temp) / (str(index) + "-" + Path(member.filename).name)
                _extract_single_pak(archive, member, candidate)
                target = _target(stage, member.filename)
                if target.casefold() in seen_targets:
                    raise ValueError("Bundle has multiple PAKs targeting " + target)
                seen_targets.add(target.casefold())
                changed = _verify(candidate, target, stage)
                entries.append(ModReview(source, target, Path(member.filename).name,
                                         member.file_size, _hash_file(candidate),
                                         changed, member.filename))
    if _hash_file(source) != archive_sha:
        raise RuntimeError("Downloaded ZIP changed during review.")
    return BundleReview(source, tuple(entries), archive_sha)


def review_legacy_folder(folder, stage, game_root=None):
    """Discover existing user-owned PAKs on demand; never import staged/live PAKs."""
    folder = Path(folder).resolve(strict=True)
    stage = Path(stage).resolve(strict=True)
    if not folder.is_dir() or folder == stage or stage in folder.parents or folder in stage.parents:
        raise ValueError("Select a separate older Mods folder, not the signing stage.")
    if game_root:
        game = Path(game_root).resolve()
        if folder == game or game in folder.parents or folder in game.parents:
            raise ValueError("Never import directly from installed game files.")
    files = []
    for path in folder.rglob("*.pak"):
        if len(files) >= 64:
            raise ValueError("Too many legacy PAKs. Select a smaller mod folder.")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ARCHIVE_SIZE:
            raise ValueError("Legacy folder contains an unsafe or oversized PAK.")
        files.append(path)
    if not files:
        raise ValueError("The selected folder contains no .pak files.")
    entries, targets = [], set()
    for candidate in files:
        target = _locate_target(stage, candidate.name)
        if target.casefold() in targets:
            raise ValueError("Multiple legacy PAKs target " + target + "; choose one.")
        targets.add(target.casefold())
        entries.append(ModReview(candidate, target, candidate.name,
                                 candidate.stat().st_size, _hash_file(candidate),
                                 _verify(candidate, target, stage)))
    return BundleReview(folder, tuple(entries), legacy=True)


def import_reviewed_bundle(bundle, stage, catalog_root, profile_id,
                           *, replace_conflicts=False):
    """Verify every PAK again; register all-or-none via one atomic catalog commit."""
    from mod_profiles import ModProfiles
    if not isinstance(bundle, BundleReview) or not bundle.entries:
        raise ValueError("Review all bundle PAKs before importing.")
    stage = Path(stage).resolve(strict=True)
    catalog_root = Path(catalog_root).resolve()
    store = ModProfiles(catalog_root)
    new_files = []
    with tempfile.TemporaryDirectory(prefix="evolve-bundle-import-") as temp:
        prepared = []
        if bundle.archive_sha is not None:
            if _hash_file(bundle.source) != bundle.archive_sha:
                raise RuntimeError("Bundle ZIP changed since review.")
            with zipfile.ZipFile(bundle.source) as archive:
                contents = {x.filename: x for x in _members(archive)}
                if set(contents) != {entry.zip_member for entry in bundle.entries}:
                    raise RuntimeError("ZIP member list changed since review.")
                for index, entry in enumerate(bundle.entries):
                    output = Path(temp) / (str(index) + "-" + entry.pak_filename)
                    _extract_single_pak(archive, contents[entry.zip_member], output)
                    prepared.append((entry, output))
        else:
            prepared = [(entry, entry.source.resolve(strict=True)) for entry in bundle.entries]
        # FIRST validate the whole bundle; no library/catalog writes until then.
        for entry, path in prepared:
            if _hash_file(path) != entry.sha256:
                raise RuntimeError("PAK changed since review: " + entry.target)
            target = (_target(stage, entry.zip_member) if entry.zip_member else
                      _locate_target(stage, entry.pak_filename))
            if target.casefold() != entry.target.casefold():
                raise RuntimeError("Signing stage target changed: " + entry.target)
            if _verify(path, target, stage) != entry.changed_entries:
                raise RuntimeError("Signed PAK asset changes differ from review: " + target)
        # Dedupe via full SHA256, not prefix alone. Never hardlink untrusted sources.
        chosen = []
        try:
            for entry, candidate in prepared:
                destination = (catalog_root / "ImportedMods" / entry.sha256 /
                               entry.pak_filename)
                if destination.exists():
                    if destination.is_symlink() or _hash_file(destination) != entry.sha256:
                        raise RuntimeError("An existing library path has an unexpected hash.")
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temp_path = destination.with_suffix(".pak.new")
                    try:
                        with candidate.open("rb") as src, temp_path.open("xb") as dst:
                            shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
                            dst.flush()
                            os.fsync(dst.fileno())
                        if _hash_file(temp_path) != entry.sha256:
                            raise RuntimeError("Copied PAK checksum mismatch.")
                        os.replace(temp_path, destination)
                        new_files.append(destination)
                    finally:
                        temp_path.unlink(missing_ok=True)
                chosen.append((destination, entry.target, entry.sha256))
            ids, already = store.add_verified_bundle(
                profile_id, chosen, stage, replace_conflicts=replace_conflicts,
                display_name=bundle.source.stem[:70])
        except BaseException:
            # Remove only newly imported copies. Existing shared library data stays.
            for destination in new_files:
                destination.unlink(missing_ok=True)
            raise
    return ids, tuple(path for path, _, _ in chosen), already
