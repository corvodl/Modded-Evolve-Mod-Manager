"""Developer diagnostic support archive with automatic secret/IP redaction.

Collects only user-selected game logs; never exports raw process memory,
credentials, tokens, packet payloads or contents of game account databases.
"""
from pathlib import Path
import json
import os
import zipfile

from developer_network_lab import redact

MAX_FILES=12
MAX_LOG_BYTES=8*1024*1024
KNOWN_LOGS={'game.log','rest.log','localization.log','kandoc.log','stage2trace.log',
            'launcher.log','client.log','error.log'}


def discover_logs(game_root):
    root=Path(game_root).resolve()
    result=[]
    for directory in (root,root/'logs',root/'EvolveGame',
                      root/'EvolveGame'/'logs'):
        if not directory.is_dir():
            continue
        for file in directory.iterdir():
            if (file.is_file() and not file.is_symlink() and
                    file.name.casefold() in KNOWN_LOGS and
                    file.stat().st_size<=MAX_LOG_BYTES):
                result.append(file)
    return tuple(result[:MAX_FILES])


def create_support_archive(game_root,destination,*,selected=None):
    """Use an explicit new destination; archive is local and reviewable."""
    destination=Path(destination)
    if destination.exists() or destination.suffix.casefold()!='.zip':
        raise ValueError('Choose a new ZIP destination.')
    files=discover_logs(game_root)
    if selected is not None:
        chosen={str(Path(path).resolve()) for path in selected}
        files=tuple(file for file in files if str(file.resolve()) in chosen)
    results=[]
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as zipf:
        for i,path in enumerate(files):
            text=path.read_text('utf-8',errors='replace')
            clean='\n'.join(redact(x) for x in text.splitlines())
            name='%02d_%s.txt'%(i,path.stem.lower())
            zipf.writestr(name,clean)
            results.append({'file':name,'chars':len(clean)})
        zipf.writestr('manifest.json',json.dumps({
            'format':'evolve-redacted-support-archive-v1',
            'files':results,
            'note':'No raw game memory, account tokens, or packet contents included'
        },indent=2))
    return tuple(results)
