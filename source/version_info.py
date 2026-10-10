"""Version display uses the packaged version manifest, not a hardcoded UI string."""
from pathlib import Path
import re


def app_version(path=None):
    if path is None:
        path = Path(__file__).resolve().parent / 'VERSION.txt'
    try:
        first = Path(path).read_text(encoding='utf-8').strip().split(' - ', 1)[0]
        if re.fullmatch(r'\d+\.\d+(?:\.\d+)?', first):
            return 'v' + first
    except OSError:
        pass
    return 'Development build'
