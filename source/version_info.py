"""Version display uses the packaged version manifest, not a hardcoded UI string."""
from pathlib import Path
import re
import sys


def app_version(path=None):
    if path is None:
        path = Path(__file__).resolve().parent / 'VERSION.txt'
    try:
        first = Path(path).read_text(encoding='utf-8').strip().split(' - ', 1)[0]
        if re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?(?:-(?:alpha|beta|rc)\.\d+)?', first):
            revision_file = (Path(sys.executable).resolve().parent / 'BUILD_REVISION.txt'
                             if getattr(sys, 'frozen', False) and path is None
                             else Path(path).resolve().parent / 'BUILD_REVISION.txt')
            revision = revision_file.read_text('ascii').strip() if revision_file.is_file() else '0'
            if re.fullmatch(r'[1-9][0-9]{0,4}', revision):
                return 'v' + first + ' · Patch ' + revision
            return 'v' + first
    except OSError:
        pass
    return 'Development build'
