"""Reject accidental FFDec or Java-runtime redistribution in app-only releases.

FFDec's third-party files and a Java runtime are deliberately user-installed.
The manager only includes code that talks to that external installation.
This policy is applied to both the on-disk build and the portable ZIP.
"""
from __future__ import annotations

from pathlib import Path


_EXTERNAL_EXECUTABLES = frozenset({
    "ffdec.exe", "ffdec-cli.exe", "ffdec.jar", "java.exe",
    "javaw.exe", "javac.exe", "jvm.dll", "jpackage.exe",
    "keytool.exe", "jfr.exe", "jshell.exe", "java.dll",
})


def external_dependency_in_path(relative):
    """Return why a packaged member violates the FFDec / Java policy, if any."""
    parts = [p.casefold() for p in Path(str(relative).replace("\\", "/")).parts]
    if not parts:
        return None
    name = parts[-1]
    if name in _EXTERNAL_EXECUTABLES or name.startswith(("ffdec_", "ffdec-", "ffdec.")):
        return "FFDec or Java executable/archive"
    for component in parts[:-1]:
        if component in ("ffdec", "jre", "jdk", "java-runtime", "java_runtime"):
            return "FFDec or Java runtime directory"
        if component.startswith(("jre-", "jdk-", "temurin-", "openjdk-", "ffdec-")):
            return "FFDec or Java distribution directory"
    # Do not treat the manager's own qt_ffdec_prompt.py as an FFDec program.
    return None


def assert_external_dependencies_absent(paths):
    """Accepts relative names from the built folder, ZIP or installer manifest."""
    disallowed = [(str(p), external_dependency_in_path(p)) for p in paths]
    disallowed = [p for p, why in disallowed if why]
    if disallowed:
        sample = ", ".join(disallowed[:8])
        raise ValueError(
            "Refusing to bundle FFDec or Java (install separately): " + sample)


def check_app_folder(root):
    root = Path(root)
    assert_external_dependencies_absent(
        item.relative_to(root).as_posix() for item in root.rglob("*"))
