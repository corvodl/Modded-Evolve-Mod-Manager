"""Publish experimental Qt package without touching stable Main release."""
from __future__ import annotations
from pathlib import Path
import sys
import zipfile

def package(root: Path, output_dir: Path):
    root = Path(root).resolve(strict=True)
    output_dir = Path(output_dir).resolve()
    for name in ('EvolveModManagerQt.exe','EvolveModWorker.exe',
                 'EvolveModLegacyEditor.exe','BUILD_COMMIT.txt','BUILD_CHANNEL.txt'):
        if not (root/name).is_file():
            raise FileNotFoundError('Qt package missing: '+name)
    if (root/'Data').exists():
        raise RuntimeError('Refuse to package any user Data folder.')
    channel = (root/'BUILD_CHANNEL.txt').read_text(encoding='utf-8').strip()
    if channel != 'qt-preview':
        raise RuntimeError('Qt package must not masquerade as Main channel.')
    output_dir.mkdir(parents=True,exist_ok=True)
    target = output_dir/'EvolveModManagerQt-Preview-Windows.zip'
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,
                         allowZip64=True) as archive:
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part.casefold() in ('data','gamesets','mykeys') for part in rel.parts):
                raise RuntimeError('Private data found in Qt package: '+str(rel))
            if path.suffix.casefold() == '.pak' or path.name.casefold() == 'private_key.pem':
                raise RuntimeError('Game PAK or private key in Qt package: '+str(rel))
            archive.write(path, str(Path('EvolveModManagerQt')/rel))
    print('Experimental Qt ZIP ready:',target,flush=True)
    return target

if __name__=='__main__':
    if len(sys.argv)!=3:
        raise SystemExit('Usage: qt_preview_package.py <build-folder> <output-folder>')
    package(Path(sys.argv[1]),Path(sys.argv[2]))
