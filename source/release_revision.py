"""Determine a patch revision without changing the public milestone version.

Workflow fetches a verified previous rolling-channel manifest, then calls this
pure helper. Pushes with the same version advance the patch; an explicit patch
run refuses to silently create a different milestone.
"""
import argparse
import json
from pathlib import Path
import re
from app_updater import version_key

VERSION = re.compile(r'\d+\.\d+\.\d+(?:\.\d+)?(?:-(?:alpha|beta|rc)\.\d+)?')


def next_revision(version, previous=None, mode='auto'):
    version = str(version).split(' - ', 1)[0].removeprefix('v')
    if not VERSION.fullmatch(version):
        raise ValueError('Invalid milestone version.')
    if mode not in ('auto', 'patch', 'milestone'):
        raise ValueError('Invalid publishing mode.')
    if previous is not None:
        old_version = str(previous.get('version', '')).split(' - ', 1)[0].removeprefix('v')
        old_revision = previous.get('revision', 0)
        if (previous.get('schema') not in (1, 2) or
                type(old_revision) is not int or not 0 <= old_revision <= 99999):
            raise ValueError('Previous manifest is not trustworthy.')
        if version_key(version) < version_key(old_version):
            raise ValueError('Cannot publish an older milestone version.')
        if old_version == version:
            if mode == 'milestone':
                raise ValueError('Milestone exists; use patch publishing.')
            if old_revision >= 99999:
                raise ValueError('Maximum revision reached.')
            return old_revision + 1, 'patch'
    if mode == 'patch':
        raise ValueError('Patch requires an existing release with identical version.')
    return 0, 'milestone'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--previous')
    parser.add_argument('--mode', choices=('auto', 'patch', 'milestone'), default='auto')
    args = parser.parse_args()
    previous = json.loads(Path(args.previous).read_text(encoding='utf-8')) if args.previous else None
    revision, kind = next_revision(args.version, previous, args.mode)
    print(json.dumps({'revision': revision, 'release_type': kind}, separators=(',', ':')))


if __name__ == '__main__':
    main()
