"""Guarded Stage 2 PAK deployment into an *isolated* staging directory.

This consumes already-built signed archives; it does not convert Legacy payloads,
sign new archives, prove CryEngine mounts new filenames, or edit live game menus.
The caller MUST supply a verifier for every archive. The UI does not call this
module until the signing and mount/discovery gates have been validated.

The receipt is a recovery journal. Never delete it until restoration is complete.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

RECEIPT = "transfer-recovery.json"
STAGING_MARKER = ".stage2-transfer-staging.json"
BUFFER = 1024 * 1024
HEX = re.compile(r"^[0-9a-fA-F]{64}$")
MAX_FILES = 512


class TransferCancelled(RuntimeError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for data in iter(lambda: stream.read(BUFFER), b""):
            h.update(data)
    return h.hexdigest()


def _digest(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("Expected a 64-digit SHA-256 digest.")
    return value.lower()


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Invalid transfer path.")
    parts = value.split("/")
    if (any(part in ("", ".", "..") or ":" in part for part in parts)
            or value.startswith("/") or not value.casefold().endswith(".pak")):
        raise ValueError("Only safe, relative .pak archive paths are allowed.")
    return parts


def _path(root, value):
    root = Path(root).absolute()
    path = root.joinpath(*_relative(value))
    # Check the actual filesystem, not only lexical traversal.
    for current in (path, *path.parents):
        if current.is_symlink():
            raise ValueError("Symbolic links and linked parent paths are not allowed.")
        if current == root:
            break
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Transfer path escapes its root.")
    return path


def _write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as dst:
            json.dump(value, dst, indent=2, sort_keys=True)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _copy_verified(source, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        with Path(source).open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, BUFFER)
            dst.flush()
            os.fsync(dst.fileno())
        if sha256_file(temp) != expected:
            raise ValueError("Copied archive integrity check failed.")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def crypak_verifier(public_key):
    """Return a verifier using the manager's actual signed CryPak reader."""
    key = Path(public_key).resolve(strict=True)
    def verify(path, relative):
        from evolve_pak_workspace import extract_plain_cdr
        _, _, _, signed_name = extract_plain_cdr(Path(path), key)
        if signed_name.casefold() != Path(relative).name.casefold():
            raise ValueError("Signed CryPak basename does not match destination.")
    return verify


def _parse_package(package, rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("Expected 1-512 explicit archive operations.")
    prepared, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid package row.")
        rel = row.get("path")
        _relative(rel)
        key = rel.casefold()
        if key in seen:
            raise ValueError("Duplicate destination (case-insensitive): " + rel)
        seen.add(key)
        op = row.get("operation")
        if op not in ("add", "replace"):
            raise ValueError("Unknown operation.")
        incoming = _digest(row.get("sha256"))
        previous = _digest(row.get("baseline_sha256")) if op == "replace" else None
        if op == "add" and row.get("baseline_sha256") is not None:
            raise ValueError("An added file cannot have a replacement baseline.")
        src = _path(package, row.get("source"))
        if not src.is_file() or sha256_file(src) != incoming:
            raise ValueError("Missing or changed package archive: " + rel)
        prepared.append(dict(path=rel, source=row["source"], operation=op,
                             sha256=incoming, baseline_sha256=previous))
    return prepared


def install_package(package, target_root, recovery, *, verify_archive,
                    allow_new_archives=False, cancelled=None):
    """Install verified archive bytes into caller-provided STAGING, not game.

    New filenames are opt-in because CryEngine mount discovery remains unproven.
    A separate runtime test must establish that the staged filename is loaded.
    No signing or conversion is performed.
    """
    if not callable(verify_archive):
        raise ValueError("A real signed-archive verifier is mandatory.")
    package = Path(package).resolve(strict=True)
    target = Path(target_root).resolve(strict=True)
    recovery = Path(recovery).absolute()
    if (not package.is_dir() or not target.is_dir() or recovery.exists()
            or recovery.is_symlink() or
            any(recovery.is_relative_to(root) or root.is_relative_to(recovery)
                for root in (package, target))):
        raise ValueError("Package, staging target and new recovery folder must be disjoint.")
    marker = target / STAGING_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("Target is not a marked isolated transfer staging directory.")
    marker_data = json.loads(marker.read_text("utf-8"))
    if marker_data != {"purpose": "isolated-stage2-transfer-staging", "schema": 1}:
        raise ValueError("Invalid isolated staging marker.")
    data = json.loads((package / "transfer-package.json").read_text("utf-8"))
    if data.get("schema") != 1 or data.get("kind") != "stage2-verified-pak-transfer":
        raise ValueError("Unsupported package manifest.")
    rows = _parse_package(package, data.get("files"))
    if any(row["operation"] == "add" for row in rows) and not allow_new_archives:
        raise ValueError("New PAK discovery is unverified; explicit opt-in required.")
    # Complete preflight: validate ALL destinations and ALL signatures before writes.
    for row in rows:
        if cancelled and cancelled():
            raise TransferCancelled("Cancelled before staging.")
        destination = _path(target, row["path"])
        old = destination.is_file()
        if row["operation"] == "add" and destination.exists():
            raise FileExistsError("New archive already exists: " + row["path"])
        if row["operation"] == "replace" and (
                not old or sha256_file(destination) != row["baseline_sha256"]):
            raise ValueError("Staged baseline mismatch: " + row["path"])
        verify_archive(_path(package, row["source"]), row["path"])
    recovery.mkdir(parents=True, exist_ok=False)
    receipt = dict(schema=1, phase="backing-up",
                   package=str(package), target=str(target), files=rows)
    try:
        for row in rows:
            if row["operation"] == "replace":
                src = _path(target, row["path"])
                backup = _path(recovery, row["path"])
                _copy_verified(src, backup, row["baseline_sha256"])
        receipt["phase"] = "installing"
        _write_json(recovery / RECEIPT, receipt)
        for row in rows:
            if cancelled and cancelled():
                raise TransferCancelled("Cancelled during staging.")
            dst = _path(target, row["path"])
            if row["operation"] == "add" and dst.exists():
                raise FileExistsError("Untracked new file appeared: " + row["path"])
            if row["operation"] == "replace" and sha256_file(dst) != row["baseline_sha256"]:
                raise ValueError("Staged archive changed during install: " + row["path"])
            _copy_verified(_path(package, row["source"]), dst, row["sha256"])
        receipt["phase"] = "installed"
        _write_json(recovery / RECEIPT, receipt)
    except BaseException:
        if (recovery / RECEIPT).is_file():
            try:
                restore_package(recovery)
            except Exception:
                # Keep receipt and backups for manual recovery. Never discard.
                pass
        raise
    return recovery / RECEIPT


def restore_package(recovery):
    """Undo only this journal's bytes; reject any intervening user edits."""
    recovery = Path(recovery).resolve(strict=True)
    receipt_path = recovery / RECEIPT
    receipt = json.loads(receipt_path.read_text("utf-8"))
    if receipt.get("schema") != 1 or receipt.get("phase") not in (
            "installed", "installing", "restored"):
        raise ValueError("Recovery journal is invalid or backups incomplete.")
    if receipt["phase"] == "restored":
        return False
    target = Path(receipt["target"]).resolve(strict=True)
    if recovery.is_relative_to(target) or target.is_relative_to(recovery):
        raise ValueError("Unsafe recovery directory.")
    rows = receipt["files"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError("Recovery journal has invalid archive operations.")
    # Check all touched destinations first. Never clobber unrelated user edits.
    for row in rows:
        if row.get("operation") not in ("add", "replace"):
            raise ValueError("Invalid recovery operation.")
        _digest(row.get("sha256"))
        if row["operation"] == "replace":
            _digest(row.get("baseline_sha256"))
        dst = _path(target, row["path"])
        if dst.exists():
            actual = sha256_file(dst)
            if actual not in (row["sha256"], row["baseline_sha256"]):
                raise ValueError("Archive was edited after installation: " + row["path"])
        elif row["operation"] == "replace":
            raise ValueError("Replaced archive is missing: " + row["path"])
        if row["operation"] == "replace":
            saved = _path(recovery, row["path"])
            if not saved.is_file() or sha256_file(saved) != row["baseline_sha256"]:
                raise ValueError("Backup missing or corrupt: " + row["path"])
    for row in reversed(rows):
        dst = _path(target, row["path"])
        if row["operation"] == "add":
            if dst.exists():
                dst.unlink()  # Only after verifying its exact installed hash above.
        elif sha256_file(dst) != row["baseline_sha256"]:
            _copy_verified(_path(recovery, row["path"]), dst, row["baseline_sha256"])
    receipt["phase"] = "restored"
    _write_json(receipt_path, receipt)
    return True
