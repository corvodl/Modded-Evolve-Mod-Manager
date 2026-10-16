"""Install the independently distributed Evolve Blender extension into a user Blender profile.

Blender 4.2+ supports: blender --command extension install-file -r user_default -e ZIP
This tool only installs a specifically pinned offline ZIP through Blender's own
user extension repository. It never touches the game, Blender program files or
any arbitrary Python code on the manager's behalf.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile

PACKAGE_NAME = "Evolve_Animation_Browser_v1.1.0.zip"
PACKAGE_SHA256 = "b2f25ab3485217930cb7e1181ca9fe37b5fd020b34894fa9f6847b6565ccb39b"
PACKAGE_ID = "evolve_animation_browser"
PACKAGE_VERSION = "1.1.0"
PACKAGE_FILES = frozenset({
    "blender_manifest.toml", "__init__.py", "core.py", "legacy_bridge.py",
    "README.md", "LICENSE.txt",
})
MAX_ZIP_BYTES = 4 * 1024 * 1024
MIN_BLENDER = (4, 2, 0)


def addon_package_candidates(*, application=None, home=None):
    """Search only the app's own folder and exact official Download filename."""
    application = Path(application or Path(__file__).resolve().parent)
    home = Path(home or Path.home())
    return (
        application / "third_party" / PACKAGE_NAME,
        application.parent / PACKAGE_NAME,
        home / "Downloads" / PACKAGE_NAME,
    )


def find_addon_package(*, application=None, home=None):
    for path in addon_package_candidates(application=application, home=home):
        if path.is_file() and not path.is_symlink():
            return path
    return None


def blender_candidates(*, environment=None):
    """Identify familiar Windows installations without recursive disk scans."""
    env = dict(os.environ if environment is None else environment)
    found = []
    def add(path):
        candidate = Path(path)
        if candidate.is_file() and candidate.name.casefold() in ("blender.exe", "blender"):
            resolved = candidate.resolve()
            if resolved not in found:
                found.append(resolved)
    executable = shutil.which("blender")
    if executable:
        add(executable)
    if os.name == "nt":
        for key in ("PROGRAMFILES", "PROGRAMW6432", "LOCALAPPDATA"):
            base = env.get(key)
            if not base:
                continue
            root = Path(base)
            parents = (root / "Blender Foundation", root / "Programs" / "Blender Foundation")
            for parent in parents:
                if not parent.is_dir():
                    continue
                add(parent / "Blender" / "blender.exe")
                for child in parent.glob("Blender *"):
                    add(child / "blender.exe")
    return tuple(found)


def validate_package(package):
    """Pin the exact distributed ZIP; forbid tampered or substituted code."""
    path = Path(package)
    if path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".zip":
        raise ValueError("Choose the official Evolve Animation Browser ZIP.")
    size = path.stat().st_size
    if not 1 <= size <= MAX_ZIP_BYTES:
        raise ValueError("Evolve Animation Browser ZIP exceeds the expected size.")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != PACKAGE_SHA256:
        raise ValueError(
            "The extension ZIP does not match the official v1.1.0 build. "
            "Select the original Evolve_Animation_Browser_v1.1.0.zip.")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = {entry.filename for entry in infos}
        if (len(infos) != len(PACKAGE_FILES)
                or names != PACKAGE_FILES
                or any(entry.is_dir() or "\\" in entry.filename
                       or ".." in Path(entry.filename).parts
                       or (entry.external_attr >> 16) & 0o170000 == stat.S_IFLNK
                       or entry.flag_bits & 1
                       for entry in infos)):
            raise ValueError("Unexpected files or unsafe paths in the extension ZIP.")
        manifest = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
        if (manifest.get("id") != PACKAGE_ID
                or manifest.get("version") != PACKAGE_VERSION
                or manifest.get("type") != "add-on"):
            raise ValueError("The selected ZIP is not Evolve Animation Browser v1.1.0.")
    return path.resolve(strict=True)


def blender_version(executable, *, runner=subprocess.run):
    binary = Path(executable)
    if binary.is_symlink() or not binary.is_file() or binary.name.casefold() != "blender.exe":
        # Windows only; allow Blender in a test / non-Windows environment.
        if os.name == "nt" or binary.name.casefold() != "blender":
            raise ValueError("Choose the installed Blender blender.exe executable.")
    result = runner(
        [str(binary), "--version"], capture_output=True, text=True,
        timeout=25, errors="replace")
    if result.returncode != 0:
        raise RuntimeError("Blender version check failed: " + (result.stderr or "")[-500:])
    match = re.search(r"\bBlender\s+(\d+)\.(\d+)\.(\d+)", result.stdout or "")
    if match is None:
        raise RuntimeError("Could not confirm installed Blender version.")
    version = tuple(map(int, match.groups()))
    if version < MIN_BLENDER:
        raise ValueError("Evolve Animation Browser requires Blender 4.2 or newer.")
    return version


def install_addon(executable, package, *, runner=subprocess.run):
    """Install/enable in Blender's user_default extension repository only."""
    archive = validate_package(package)
    version = blender_version(executable, runner=runner)
    args = [str(Path(executable).resolve()), "--command", "extension",
            "install-file", "-r", "user_default", "-e", str(archive)]
    try:
        result = runner(args, capture_output=True, text=True, timeout=180,
                        errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Blender extension installation timed out.") from exc
    if result.returncode:
        summary = "\n".join(
            (result.stderr or "", result.stdout or "")).strip()[-1400:]
        raise RuntimeError(
            "Blender could not install the extension into User Default.\n"
            + (summary or "Open Blender → Preferences → Get Extensions and enable a local User Default repository, then retry."))
    return "Installed Evolve Animation Browser v%s for Blender %s. Restart any open Blender windows to refresh its extensions." % (
        PACKAGE_VERSION, ".".join(map(str, version)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", required=True)
    parser.add_argument("--package", required=True)
    args = parser.parse_args(argv)
    try:
        print(install_addon(args.blender, args.package), flush=True)
        return 0
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
        print("Blender add-on installation failed: " + str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
