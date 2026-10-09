#!/usr/bin/env python3
"""Stage any supported custom-signed Evolve PAK for the existing one-click swap.

Does NOT modify the live game directory. Saves per-update rollback material.
Backup operations are guarded against stale or mismatched content.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import uuid

from evolve_pak_rekey import load_with_signed_basename

DEFAULT_STAGE = Path(r'C:\EvolvePakTools\CustomSigning\staged')
DEFAULT_SWAP = Path(r'C:\EvolvePakTools\PauseSwapTest')
STATE_NAME = 'controlled_swap_state.json'


def digest(file):
    h = hashlib.sha256()
    with Path(file).open('rb') as stream:
        for chunk in iter(lambda:stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, obj):
    path = Path(path)
    temp = path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def ensure_closed():
    if os.name != 'nt':
        return
    result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Could not check running Evolve processes; close them and retry')
    names = {row[0].casefold() for row in csv.reader(result.stdout.splitlines()) if row}
    if {'evolve.exe', 'moddedevolvelauncher.exe'} & names:
        raise RuntimeError('Close Evolve AND the Modded Evolve Launcher before updating staging')


def canonical_rel(raw):
    rel = raw.replace('\\', '/')
    parts = PurePosixPath(rel).parts
    if (not rel or rel.startswith('/') or re.match(r'^[A-Za-z]:', rel)
            or not parts or any(s in ('..', '.', '') for s in parts) or not rel.lower().endswith('.pak')):
        raise ValueError(f'Unsafe or non-PAK staged path: {raw!r}')
    return '/'.join(parts)


def allowed_paks(stage):
    plan = read_json(stage / 'rekey_plan.json')
    rows = {}
    for entry in plan.get('entries', []):
        if entry.get('kind') != 'signed-encrypted':
            continue
        rel = canonical_rel(entry['path'])
        if rel.casefold() in rows:
            raise RuntimeError('Duplicate plan archive: ' + rel)
        rows[rel.casefold()] = rel
    if not rows or plan.get('blocked'):
        raise RuntimeError('Missing signed archive list or blocked stage plan')
    return rows


def select_target(stage, relative, mod_name=None):
    lookup = allowed_paks(stage)
    key = canonical_rel(relative).casefold()
    if key not in lookup:
        raise ValueError('Selected archive is not a signed PAK in staged rekey_plan.json: ' + relative)
    rel = lookup[key]
    target = stage / 'paks' / Path(*rel.split('/'))
    if target.resolve().is_relative_to((stage / 'paks').resolve()) is False:
        raise ValueError('Archive resolves outside staging')
    if mod_name is not None and target.name.casefold() != mod_name.casefold():
        raise ValueError('Built PAK basename does not match selected archive: ' + str(target))
    return rel, target


def validate_archive_pair(old, new, pub):
    old_info,old_signed = load_with_signed_basename(old,pub)
    new_info,new_signed = load_with_signed_basename(new,pub)
    if old_signed != new_signed or old_info['count'] != new_info['count']:
        raise ValueError('Different signed filename or archive entry count')
    a,b = old_info['entries'],new_info['entries']
    if list(a) != list(b):
        raise ValueError('Changed set/order of archive entries; only replacement supported')
    changed = [name for name in a if a[name] != b[name]]
    if not changed:
        raise ValueError('No changed CDR records detected; refusing stage update')
    print(f'RSA verification passed; {len(changed)} changed entries:')
    for name in changed[:30]:print(' ',name)
    if len(changed)>30:print(' ...',len(changed)-30,'additional entries')
    return changed


def journal_for(swap_dir, rel, target, old_hash):
    jp = Path(swap_dir) / STATE_NAME
    if not jp.is_file():
        return None, None, None, jp
    state = read_json(jp)
    if state.get('phase') not in ('prepared', 'restored'):
        raise RuntimeError('Swap is active or interrupted: restore live game files first')
    matches = [f for f in state.get('files',[]) if f.get('name','').replace('\\','/').casefold() == rel.casefold()]
    if len(matches)!=1:
        raise RuntimeError('Swap journal does not contain one exact record for '+rel)
    record = matches[0]
    if Path(record['source']).resolve() != target.resolve():
        raise RuntimeError('Swap journal source disagrees with staging: '+rel)
    ready = Path(record['ready'])
    if state['phase']=='prepared':
        if not ready.is_file():
            raise RuntimeError('Prepared swap archive is missing: '+str(ready))
        if digest(ready)!=old_hash or record.get('sha256','').lower()!=old_hash:
            raise RuntimeError('Prepared swap file and journal differ from staged source: '+rel)
    elif ready.exists():
        raise RuntimeError('Restored swap journal still has a ready archive: '+rel)
    return state,record,ready,jp


def copy_checked(src, dst, expected):
    dst=Path(dst)
    temp=dst.with_name(dst.name+'.manager-temp-'+uuid.uuid4().hex)
    try:
        shutil.copyfile(src,temp)
        if digest(temp) != expected:
            raise RuntimeError('Copied archive hash mismatch: '+str(temp))
        os.replace(temp,dst)
    finally:
        temp.unlink(missing_ok=True)


def install(stage, swap_dir, backup_root, relative, mod):
    ensure_closed()
    stage, swap_dir, backup_root, mod = map(lambda p:Path(p).resolve(),(stage,swap_dir,backup_root,mod))
    rel,target=select_target(stage,relative,mod.name)
    pub = stage / 'mykeys' / 'public_key.bin'
    if not pub.is_file() or not mod.is_file() or not target.is_file():
        raise FileNotFoundError('Public key, original staged PAK, or built PAK is missing')
    if target.resolve() == mod.resolve():
        raise ValueError('Refusing to stage an archive over itself')
    changed = validate_archive_pair(target,mod,pub)
    old_hash=digest(target);new_hash=digest(mod)
    if old_hash == new_hash:
        raise ValueError('Output archive is identical to staged source')
    state,record,ready,jp = journal_for(swap_dir,rel,target,old_hash)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    backup=backup_root/Path(*rel.split('/'))/stamp
    backup.mkdir(parents=True, exist_ok=False)
    metadata={'relative':rel, 'stage':str(stage),'swap_dir':str(swap_dir),
              'old_sha256':old_hash,'new_sha256':new_hash,'source':str(target),
              'has_ready':state is not None and state['phase']=='prepared',
              'status':'backup-created','changed_count':len(changed)}
    save_json(backup/'metadata.json',metadata)
    try:
        shutil.copy2(target,backup/'before.pak')
        if digest(backup/'before.pak') != old_hash:
            raise RuntimeError('Backup hash mismatch')
        if metadata['has_ready']:
            shutil.copy2(ready,backup/'ready-before.pak')
            if digest(backup/'ready-before.pak') != old_hash:
                raise RuntimeError('Prepared backup mismatch')
        metadata['status']='backed-up'
        save_json(backup/'metadata.json',metadata)
        copy_checked(mod,target,new_hash)
        if metadata['has_ready']:
            copy_checked(mod,ready,new_hash)
            record['sha256']=new_hash
            record['bytes']=mod.stat().st_size
            save_json(jp,state)
        metadata['status']='installed'
        save_json(backup/'metadata.json',metadata)
    except BaseException:
        # Best-effort immediate rollback, after verified backups.
        try:
            if (backup/'before.pak').is_file() and digest(backup/'before.pak')==old_hash:
                copy_checked(backup/'before.pak',target,old_hash)
            if metadata['has_ready'] and (backup/'ready-before.pak').is_file() and digest(backup/'ready-before.pak')==old_hash:
                copy_checked(backup/'ready-before.pak',ready,old_hash)
                if jp.exists():
                    cur=read_json(jp)
                    ms=[x for x in cur.get('files',[]) if x.get('name','').replace('\\','/').casefold()==rel.casefold()]
                    if len(ms)==1 and cur.get('phase')=='prepared':
                        ms[0]['sha256']=old_hash;ms[0]['bytes']=target.stat().st_size
                        save_json(jp,cur)
            metadata['status']='rolled-back-after-error'
            save_json(backup/'metadata.json',metadata)
        except Exception as e:
            metadata['status']='NEEDS-MANUAL-RECOVERY'
            save_json(backup/'metadata.json',metadata)
            print('CRITICAL: Automatic rollback incomplete: ',e,file=sys.stderr)
            print('Keep your backups: ',backup,file=sys.stderr)
        raise
    print('STAGED SUCCESSFULLY:',rel)
    print('Backed up prior staged archive:',backup)
    if metadata['has_ready']:
        print('Updated prepared one-click swap archive and its journal.')
    else:
        print('No prepared swap updated; run 01-Prepare.ps1 before the next launch.')
    print('The live game installation was NOT touched.')
    return backup


def restore(backup):
    ensure_closed()
    backup=Path(backup).resolve()
    meta=read_json(backup/'metadata.json')
    if meta['status']!='installed':
        raise RuntimeError('Backup status is not installed: '+meta['status'])
    stage=Path(meta['stage']); swap_dir=Path(meta['swap_dir'])
    rel,target=select_target(stage,meta['relative'])
    if Path(meta['source']).resolve()!=target.resolve():
        raise RuntimeError('Backup was created for a different staging target')
    old,new=meta['old_sha256'],meta['new_sha256']
    if digest(target)!=new:
        raise RuntimeError('Current staged archive differs from backed-up mod; refusing to overwrite later changes')
    original=backup/'before.pak'
    if not original.is_file() or digest(original)!=old:
        raise RuntimeError('Prior staged archive backup is missing/corrupt')
    state,record,ready,jp = journal_for(swap_dir,rel,target,new)
    if meta['has_ready'] and (state is None or state['phase']!='prepared'):
        raise RuntimeError('Expected a prepared swap journal; do not restore until the same prepared state exists')
    if not meta['has_ready'] and state is not None and state['phase']=='prepared':
        raise RuntimeError('A prepared swap now exists; restore live and prepared state first before staging rollback')
    if meta['has_ready']:
        prior_ready=backup/'ready-before.pak'
        if not prior_ready.is_file() or digest(prior_ready)!=old:
            raise RuntimeError('Prior prepared archive backup is corrupt')
    copy_checked(original,target,old)
    if meta['has_ready']:
        copy_checked(prior_ready,ready,old)
        record['sha256']=old
        record['bytes']=original.stat().st_size
        save_json(jp,state)
    meta['status']='restored';save_json(backup/'metadata.json',meta)
    print('RESTORED prior staged PAK:',rel)
    print('Live installed game files untouched.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='action',required=True)
    i=sub.add_parser('install',help='Install built signed PAK into custom staging, with rollback')
    i.add_argument('--stage',type=Path,default=DEFAULT_STAGE)
    i.add_argument('--swap-dir',type=Path,default=DEFAULT_SWAP)
    i.add_argument('--backup-root',type=Path,required=True)
    i.add_argument('--relative',required=True,help='Game/libs.pak, Game/UI_Data.pak, etc')
    i.add_argument('--mod',type=Path,required=True)
    r=sub.add_parser('restore',help='Restore exactly one previous staging update')
    r.add_argument('--backup',type=Path,required=True)
    args=p.parse_args()
    try:
        if args.action=='install':install(args.stage,args.swap_dir,args.backup_root,args.relative,args.mod)
        else:restore(args.backup)
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':sys.exit(main())
