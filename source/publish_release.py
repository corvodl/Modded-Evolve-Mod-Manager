"""Publish a clean app-only release without replacing an initialized app folder."""
from datetime import datetime
from pathlib import Path
import shutil
import sys
import zipfile


def publish(built,output):
    built,output=Path(built).resolve(),Path(output).resolve()
    for name in ('EvolveModManager.exe','EvolveModWorker.exe','_internal','BUILD_COMMIT.txt','BUILD_CHANNEL.txt'):
        if not (built/name).exists():raise ValueError('Incomplete app build: '+name)
    if (built/'Data').exists() or (built/'portable_bundle.json').exists() or any(built.rglob('private_key.pem')) or any(built.rglob('*.pak')):
        raise ValueError('App-only release must not contain game PAKs or personal setup data. Build to the separate build-output folder.')
    allowed={'EvolveModManager.exe','EvolveModWorker.exe','_internal','Docs','START-HERE.txt','BUILD_COMMIT.txt','BUILD_CHANNEL.txt'}
    if any(p.name not in allowed for p in built.iterdir()):raise ValueError('Unexpected file in clean app build.')
    output.mkdir(parents=True,exist_ok=True)
    release=output/('EvolveModManager-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    shutil.copytree(built,release)
    archive=output/'EvolveModManager-Windows.zip'
    temp=archive.with_name(archive.name+'.new')
    try:
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            for p in sorted(release.rglob('*')):
                if p.is_symlink():raise ValueError('Linked runtime path unsupported.')
                if p.is_file():z.write(p,'EvolveModManager/'+p.relative_to(release).as_posix())
        with zipfile.ZipFile(temp) as z:
            bad=z.testzip()
            if bad:raise ValueError('Release ZIP verification failed: '+bad)
        temp.replace(archive)
    finally:temp.unlink(missing_ok=True)
    (output/'latest_release.txt').write_text(str(release),encoding='utf-8')
    print('APP:',release/'EvolveModManager.exe')
    print('SHARE:',archive)
    print('This app-only ZIP excludes game PAKs, personal keys and projects.')
    return release,archive

if __name__=='__main__':publish(sys.argv[1],sys.argv[2])
