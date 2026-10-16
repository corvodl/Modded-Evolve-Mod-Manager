"""Bounded, read-only discovery of local client profile summaries.

Only public profile fields are displayed; authentication/identity stores are
never inspected. Local cached details are not proof of current server state.
"""
from pathlib import Path
import json
import os

FIELDS = {
    'displayName': 'Display name', 'title': 'Title', 'badgeId': 'Badge',
    'bannerId': 'Banner', 'level': 'Level', 'experience': 'Experience',
    'gamesPlayed': 'Games played', 'wins': 'Wins', 'losses': 'Losses',
    'favoriteHunter': 'Favorite hunter', 'favoriteMonster': 'Favorite monster',
    'lastUpdatedUtc': 'Client updated (UTC)',
}
MAX_BYTES = 1024 * 1024


def profile_sources(client=None, local_appdata=None):
    """Return exact known locations, without scanning unrelated user files."""
    candidates = []
    if client:
        candidates.append(('Configured client folder', Path(client).parent / 'profile.json'))
    local = local_appdata if local_appdata is not None else os.environ.get('LOCALAPPDATA')
    if local:
        candidates.append(('Local launcher cache', Path(local) / 'ModdedEvolveLauncher' / 'profile.json'))
    result, seen = [], set()
    for label, path in candidates:
        key = str(path.absolute()).casefold()
        if key not in seen:
            seen.add(key)
            result.append((label, path))
    return result


def read_details(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('No local profile found at this location.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Local profile exceeds the 1 MiB inspection limit.')
    if raw.startswith(b'TRSEVLVPC'):
        raise ValueError('Encoded game cache: profile details cannot be decoded.')
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('Local profile is not readable JSON.') from exc
    if not isinstance(data, dict):
        raise ValueError('Local profile must contain a JSON object.')
    rows = []
    for key, label in FIELDS.items():
        value = data.get(key)
        if type(value) in (str, int, float) and len(str(value)) <= 500:
            rows.append((label, str(value)))
    if not rows:
        raise ValueError('No recognized client profile details in this file.')
    return rows
