"""Read-only official Modded Evolve client update manifest inspection.

The client is updated by its own updater. This module only checks the static
manifest and supplies an official download/release link; it never installs code.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MANIFEST_URL = (
    "https://raw.githubusercontent.com/MeH0y/"
    "ModdedEvolveLauncherUpdates/main/version.json"
)
RELEASE_URL = (
    "https://github.com/MeH0y/ModdedEvolveLauncherUpdates/"
    "releases/tag/Launcher_Update"
)
_VERSION = re.compile(r"\d+(?:\.\d+){1,3}\Z")
_MAX_MANIFEST_SIZE = 32768


def version_tuple(text):
    if not isinstance(text, str) or not _VERSION.fullmatch(text.strip()):
        return None
    parts = tuple(map(int, text.strip().split(".")))
    return parts + (0,) * (4 - len(parts))


def trusted_release_link(url):
    """Never put an arbitrary HTTP destination from a remote JSON into the UI."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "github.com"
            or parts.username or parts.password or parts.port
            or parts.query or parts.fragment
            or not parts.path.startswith(
                "/MeH0y/ModdedEvolveLauncherUpdates/releases/")):
        return None
    return url


def evaluate_manifest(installed, data):
    if not isinstance(data, dict):
        raise ValueError("Client manifest is not a JSON object.")
    latest = data.get("latestVersion")
    remote = version_tuple(latest)
    if remote is None:
        raise ValueError("Client manifest has no valid latestVersion.")
    local = version_tuple(installed)
    if local is None:
        status = "unknown"
        description = "Installed client version unavailable."
    elif local < remote:
        status = "outdated"
        description = "Client update available."
    elif local == remote:
        status = "current"
        description = "Client is up to date."
    else:
        status = "newer"
        description = "Installed version is newer than the published manifest."
    return {
        "status": status, "message": description,
        "latest": latest,
        "url": trusted_release_link(data.get("changelogUrl")) or RELEASE_URL,
    }


def check_client_version(installed, timeout=6):
    """Retrieve a small manifest in a background worker; safely fail unknown."""
    try:
        request = Request(MANIFEST_URL, headers={
            "User-Agent": "EvolveModManager/client-version-check",
            "Accept": "application/json",
        })
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_MANIFEST_SIZE + 1)
        if len(raw) > _MAX_MANIFEST_SIZE:
            raise ValueError("Client update manifest is too large.")
        data = json.loads(raw.decode("utf-8-sig"))
        return evaluate_manifest(installed, data)
    except (OSError, ValueError, UnicodeError, TypeError, TimeoutError) as error:
        return {
            "status": "unknown", "latest": None, "url": RELEASE_URL,
            "message": "Unable to verify the latest client version: " + str(error)[:160],
        }
